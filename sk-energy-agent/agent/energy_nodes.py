"""에너지 허브 진단 그래프 노드

Supervisor(분류) → 이름 찾기 → 워커(코드 조회) ⇄ 재계획 → 분석 에이전트(LLM 판별)
→ 권위 게이트(코드 대조·교정) → 작성 에이전트 ⇄ 검증 에이전트(코드 숫자 대조 + LLM-as-Judge)
"""
import asyncio
import time
from datetime import datetime

from langgraph.config import get_stream_writer
from langsmith import traceable

from energy import criteria
from energy.rules.competitors import watch_order_key
from energy.seoul import BRAND_ALIASES, SEOUL_GU

from .deps import SupervisorDeps
from .facts import (build_competition_facts, build_facts, build_priority_facts,
                    build_watch_facts)
from .retry import with_retry
from .validation import check_answer, check_priority

JUDGED_TYPES = ("station", "district", "scenario")
GATE_REASON = "기준에 따라 확정 (분석 에이전트 판별과 달라 교정)"
PRIORITY_ONLY_FACTS = ("busy_1km:",)
DISPLAY_ONLY_FACTS = ("ev_per_fast", "seoul_ev_per_fast")   # 1만 대당 값을 화면용으로 바꾼 것
MAX_REPLAN = 1
MAX_REWRITE = 1


def _spend(state: dict, usage: dict) -> dict:
    # LLM을 부를 때마다 비용과 호출 수를 함께 쌓는다 — 상시 감시는 이상 없으면 0회여야 함
    return {"cost_usd": round(state.get("cost_usd", 0.0) + usage["usd"], 6),
            "llm_calls": state.get("llm_calls", 0) + 1}


def _company(state: dict) -> str:
    return state.get("company") or "SK에너지"


async def call_tool(deps: SupervisorDeps, name: str, args: dict) -> dict:
    """MCP Tool을 부르고, 실패하면 예외 대신 error dict를 돌려주는 함수 (장애 격리)

    Args:
        deps: 의존성 묶음
        name: Tool 이름
        args: Tool 입력
    Returns:
        Tool 응답 또는 {"error": {...}}
    """
    try:
        data, _ = await with_retry(deps.mcp.call_tool, name, args)
        return data
    except Exception as exc:
        return {"error": {"message": type(exc).__name__, "tool": name}}


# ── Supervisor ──────────────────────────────────────────────────

def energy_route(state: dict, deps: SupervisorDeps) -> dict | None:
    """질문을 에너지 요청 종류로 나누는 Supervisor — 에너지 질문이 아니면 None

    지도에서 넘어온 station_id·gu 가 있으면 대상은 코드가 정하고,
    요청 종류(진단/시나리오)와 문장 속 대상만 LLM(fast)이 분류한다.

    Args:
        state: 그래프 상태
        deps: 의존성 묶음
    Returns:
        route·request_type·대상이 담긴 상태 변경 dict 또는 None
    """
    parsed, usage = deps.fast_llm.classify_energy(state["query"], SEOUL_GU,
                                                  tuple(BRAND_ALIASES))
    gu = parsed.get("gu") if parsed.get("gu") in SEOUL_GU else None
    scenario = parsed.get("fast_add") is not None
    update = {"fast_add": parsed.get("fast_add"), "kw": parsed.get("kw") or 100,
              **_spend(state, usage)}

    if state.get("station_id"):
        request_type = "scenario" if scenario else "station"
        route = "energy"
    elif state.get("gu") in SEOUL_GU:
        request_type, route = "district", "energy"
    elif parsed.get("station_name"):
        request_type = "scenario" if scenario else "station"
        route = "resolve"
        update.update(station_name=parsed["station_name"], brand_hint=parsed.get("brand"))
    elif gu:
        request_type, route = "district", "energy"
        update["gu"] = gu
    else:
        return None

    event = {"type": "RouteDecided", "route": route, "request_type": request_type,
             "station_id": state.get("station_id"), "gu": state.get("gu") or update.get("gu")}
    return {**update, "route": route, "request_type": request_type,
            "events": state.get("events", []) + [event]}


@traceable(name="resolve_node")
async def resolve_node(state: dict, deps: SupervisorDeps) -> dict:
    """문장 속 주유소 이름을 주유소 번호로 바꾸는 노드 — 2곳 이상이면 되묻기로 보낸다"""
    args = {"query": state["station_name"]}
    if state.get("brand_hint"):
        args["brand"] = state["brand_hint"]
    found = await call_tool(deps, "resolve_station", args)
    candidates = found.get("candidates", [])
    event = {"type": "StationResolved", "query": state["station_name"],
             "count": len(candidates),
             "station_id": candidates[0]["station_id"] if len(candidates) == 1 else None}
    if len(candidates) == 1:
        return {"station_id": candidates[0]["station_id"], "route": "energy",
                "events": state["events"] + [event]}
    return {"candidates": candidates, "route": "ask_user", "events": state["events"] + [event]}


@traceable(name="ask_user_node")
async def ask_user_node(state: dict, deps: SupervisorDeps) -> dict:
    """같은 이름 주유소가 여러 곳이거나 없을 때 되묻는 답을 만드는 노드 (LLM 없음)"""
    candidates = state.get("candidates", [])
    if not candidates:
        reply = (f"'{state['station_name']}'을(를) 서울 주유소·LPG 충전소 470곳에서 찾지 "
                 "못했습니다. 지도에서 주유소를 선택하거나 상호를 다시 알려 주세요.")
    else:
        lines = [f"'{state['station_name']}'이(가) {len(candidates)}곳 있습니다. "
                 "어느 곳인가요? (지도에서 핀을 누르면 정확히 지정됩니다)"]
        lines += [f"- {c['name']} · {c['brand']} · {c['gu']} ({c['station_id']})"
                  for c in candidates]
        reply = "\n".join(lines)
    return {"final": reply,
            "events": state["events"] + [{"type": "AskUser", "count": len(candidates)}]}


# ── 워커 (코드 조회) ────────────────────────────────────────────

def _baseline_items(request_type: str, district: dict, cluster: dict, sim: dict) -> dict:
    if request_type == "scenario":
        if "error" in sim:
            return {}
        return {"ev_bottleneck_before": sim["before"]["ev_bottleneck"],
                "ev_bottleneck_after": sim["after"]["ev_bottleneck"]}
    if "error" in district or "error" in cluster:
        return {}
    return {
        "ev_bottleneck": district["ev_bottleneck"],
        "diesel_idle": district["diesel_idle"],
        "lpg_idle": district["lpg_idle"],
        "fast_candidates": [s["station_id"] for s in sorted(
            cluster["stations"], key=lambda s: (s["fast_1km"], s["station_id"]))
            if s["verdict"] == "fast_candidate"],
    }


@traceable(name="energy_research_node")
async def energy_research_node(state: dict, deps: SupervisorDeps) -> dict:
    """자치구 수요·공급·수소, 자사 묶음, (시나리오) 계산과 기준 판정을 병렬로 조회하는 노드"""
    company, request_type = _company(state), state["request_type"]
    events = list(state["events"])
    results: dict = {}

    if state.get("station_id"):
        results["card"] = await call_tool(deps, "get_station_card", {
            "station_id": state["station_id"], "company": company, "with_verdict": False})
        if "error" in results["card"]:
            events.append({"type": "TaskCompleted", "source": "get_station_card", "ok": False})
            return {"tool_results": results, "facts": [], "is_own": False, "events": events}
    card = results.get("card")
    gu = card["gu"] if card else state["gu"]
    is_own = card["is_own"] if card else True

    tasks = {"demand": ("get_fuel_demand", {"gu": gu}),
             "supply": ("get_ev_supply", {"gu": gu}),
             "h2": ("get_h2_status", {"gu": gu}),
             "own": ("search_stations", {"gu": gu, "brand": company})}
    if request_type == "station":   # 공개 정보라 타사 거점도 경쟁 현황은 보여준다
        tasks["competition"] = ("get_nearby_competition", {"station_id": state["station_id"]})
    if is_own:   # 타사 거점은 판별하지 않으므로 기준 판정·시나리오도 부르지 않는다
        tasks["district"] = ("score_district", {"gu": gu})
        tasks["cluster"] = ("review_cluster", {"gu": gu, "brand": company})
        # 우선순위 판단에 묶음 전체의 지난 결정이 필요해 주유소 질문도 자치구 단위로 읽는다
        tasks["decisions"] = ("get_decision_log", {"company": company, "gu": gu})
        if request_type == "scenario":
            tasks["simulate"] = ("simulate", {"station_id": state["station_id"],
                                              "fast_add": state["fast_add"],
                                              "kw": state.get("kw", 100)})
    events.append({"type": "PlanCreated", "gu": gu, "tools": [t for t, _ in tasks.values()]})

    started = time.perf_counter()
    responses = await asyncio.gather(*(call_tool(deps, n, a) for n, a in tasks.values()))
    results.update(zip(tasks, responses))
    own_ids = [s["station_id"] for s in results["own"].get("stations", [])]
    results["cluster_cards"] = await asyncio.gather(*(
        call_tool(deps, "get_station_card",
                  {"station_id": sid, "company": company, "with_verdict": False})
        for sid in own_ids))
    elapsed = round(time.perf_counter() - started, 3)

    errors = [{"tool": tasks[k][0], **results[k]["error"]} for k in tasks
              if "error" in results[k]]
    for key, (name, _) in tasks.items():
        events.append({"type": "TaskCompleted", "source": name, "target": gu,
                       "ok": "error" not in results[key]})

    facts = build_facts(results, company)
    baseline = (_baseline_items(request_type, results.get("district", {}),
                                results.get("cluster", {}), results.get("simulate", {}))
                if is_own else {})
    events.append({"type": "FactsCollected", "count": len(facts), "is_own": is_own,
                   "own_stations": len(own_ids), "errors": len(errors)})
    return {"gu": gu, "is_own": is_own, "tool_results": results, "facts": facts,
            "baseline": baseline, "errors": errors, "parallel_elapsed": elapsed,
            "own_station_count": len(own_ids), "events": events}


@traceable(name="replan_node")
async def replan_node(state: dict, deps: SupervisorDeps) -> dict:
    """자치구에 자사 거점이 없을 때 가장 가까운 자사 거점 자치구로 다시 계획하는 노드"""
    company = _company(state)
    near = await call_tool(deps, "nearby_districts", {"gu": state["gu"], "brand": company})
    target = next((d["gu"] for d in near.get("districts", []) if d["own_count"] > 0), None)
    event = {"type": "Replanned", "from": state["gu"], "to": target,
             "reason": f"{state['gu']}에 {company} 거점 없음"}
    return {"gu": target or state["gu"], "replanned_from": state["gu"],
            "replan_count": state.get("replan_count", 0) + 1,
            "events": state["events"] + [event]}


# ── 분석 에이전트 → 권위 게이트 ─────────────────────────────────

@traceable(name="analyst_node")
async def analyst_node(state: dict, deps: SupervisorDeps) -> dict:
    """분석 에이전트 — 사실 [F]만 보고 판별하는 노드 (기준 판정은 넘기지 않음)"""
    # 거점별 충전 중 비율은 우선순위 판단용 — 판별 입력에 섞으면 길어져 판별이 흐려졌다 (일치율 88.9 → 77.8%)
    # 급속 1개당 전기차는 1만 대당 값과 같은 정보라 판별 입력에서 뺀다
    facts = [f for f in state["facts"]
             if not f["key"].startswith(PRIORITY_ONLY_FACTS + DISPLAY_ONLY_FACTS)]
    verdicts, usage = deps.quality_llm.judge_energy(state["request_type"], facts)
    summary = {item: v.get("value") for item, v in verdicts.items()}
    return {"llm_verdicts": verdicts, **_spend(state, usage),
            "events": state["events"] + [{"type": "Judged", "by": "analyst", **summary}]}


def _same(llm_value, base_value) -> bool:
    if isinstance(base_value, list):
        return set(llm_value or []) == set(base_value)
    return llm_value == base_value


def _ordered_candidates(llm_order: list, confirmed: list) -> list:
    # 후보 자격은 코드 기준, 후보 간 우선순위는 기준이 없어 LLM 순서를 채택한다
    kept = [sid for sid in (llm_order or []) if sid in confirmed]
    return kept + [sid for sid in confirmed if sid not in kept]


@traceable(name="authority_gate_node")
async def authority_gate_node(state: dict, deps: SupervisorDeps) -> dict:
    """권위 게이트 — 분석 에이전트 판별을 코드 기준과 대조해 확정 판정을 만드는 노드

    기준이 있는 항목은 다르면 코드 기준으로 교정하고 GateCorrected 이벤트를 남긴다.
    확정 판정마다 corrected(교정 여부)와 llm_value(AI 원래 판별)를 붙이고,
    교정된 항목은 AI의 이유 대신 "기준에 따라 확정"을 쓴다 — 틀린 판별의 이유가 맞는 결론에 붙지 않게.
    판별·기준·교정 여부는 decision_log 에 저장한다.
    """
    llm, baseline = state.get("llm_verdicts", {}), state.get("baseline", {})
    events = list(state["events"])
    verdicts, corrections = {}, []
    for n, (item, base_value) in enumerate(baseline.items(), start=1):
        mine = llm.get(item, {})
        llm_value = mine.get("value")
        corrected = not _same(llm_value, base_value)
        if corrected:
            corrections.append({"item": item, "llm": llm_value, "baseline": base_value})
            events.append({"type": "GateCorrected", "item": item,
                           "label": criteria.GATED_ITEMS[item],
                           "llm": llm_value, "baseline": base_value})
        value = (_ordered_candidates(llm_value, base_value) if isinstance(base_value, list)
                 else base_value)
        picked = set(llm_value or []) if isinstance(base_value, list) else set()
        verdicts[item] = {
            "id": f"J{n}", "value": value, "corrected": corrected, "llm_value": llm_value,
            "facts": mine.get("facts", []),
            "reason": GATE_REASON if corrected else mine.get("reason"),
            # 목록 항목은 AI가 직접 고른 거점의 이유만 남긴다 (게이트가 더한 거점엔 AI 이유가 없음)
            "reasons": {sid: r for sid, r in (mine.get("reasons") or {}).items()
                        if sid in picked and sid in (value or [])},
            "picked_by_llm": [sid for sid in (value or []) if sid in picked]
            if isinstance(base_value, list) else None,
        }

    fast = verdicts.get("fast_candidates", {})
    for sid in fast.get("value") or []:
        cited = fast["reasons"].get(sid, {}).get("facts", [])
        events.append({"type": "EvidenceCard", "station_id": sid, "verdict": "⚡",
                       "facts": cited, "picked_by": "llm" if sid in fast["picked_by_llm"]
                       else "gate"})

    request_type = state["request_type"]
    scope_type = {"station": "station", "scenario": "station", "watch": "watch",
                  "weekly": "weekly"}.get(request_type, "gu")
    job_scope = state.get("gu") or _company(state)
    scope_id = {"station": state.get("station_id"), "scenario": state.get("station_id"),
                "watch": job_scope, "weekly": job_scope}.get(request_type, state.get("gu"))
    results = state["tool_results"]
    source = results.get("district") or results.get("simulate") or results.get("job") or {}
    deps.session_store.log_decision(
        company=_company(state), scope_type=scope_type, scope_id=scope_id,
        snapshot=source.get("snapshot", ""), rule_version=criteria.RULE_VERSION,
        llm_verdict=llm, baseline=baseline, verdict=verdicts, corrections=corrections)
    events.append({"type": "Judged", "by": "gate", "corrected": len(corrections),
                   "corrected_items": [c["item"] for c in corrections],
                   **{item: v["value"] for item, v in verdicts.items()}})
    return {"verdicts": verdicts, "gate_corrections": corrections, "events": events}


# ── 분석 에이전트: 어디부터, 왜 (기준 없는 판단) ──────────────────────

@traceable(name="prioritize_node")
async def prioritize_node(state: dict, deps: SupervisorDeps) -> dict:
    """확정된 급속 후보가 2곳 이상일 때 어디부터 볼지 순서와 이유를 AI가 판단하는 노드

    후보 자격은 게이트가 정했고, 순서·이유에는 정답 규칙이 없어 AI 판단을 채택한다.
    코드는 확정 후보만 썼는지, 이유가 그 거점의 사실을 인용했는지만 확인하고,
    어기면 반경 급속이 적은 기본 순서로 돌아가며 그 사실을 남긴다.
    """
    company, gu = _company(state), state["gu"]
    candidates = list(state["verdicts"]["fast_candidates"]["value"])
    watch, weekly, *competition = await asyncio.gather(
        call_tool(deps, "detect_anomalies", {"company": company, "gu": gu}),
        call_tool(deps, "diff_competitors", {"company": company}),
        *(call_tool(deps, "get_nearby_competition", {"station_id": sid}) for sid in candidates))
    names = {f["station_id"]: f["name"] for f in state["facts"]
             if f["key"].startswith("fast_1km:")}
    facts = state["facts"] + build_priority_facts(
        len(state["facts"]), candidates, names, dict(zip(candidates, competition)), watch, weekly)
    candidate_facts = [f for f in facts if f.get("station_id") in candidates]

    result, usage = deps.quality_llm.prioritize(candidate_facts, candidates)
    issues = check_priority(result, candidates, facts)
    if issues:
        priority = {"by": "fallback", "order": candidates, "reasons": {}, "summary": None,
                    "issues": issues}
    else:
        priority = {"by": "llm", "order": result["order"], "reasons": result["reasons"],
                    "summary": result.get("summary"), "issues": []}
    event = {"type": "Prioritized", "by": priority["by"], "order": priority["order"],
             "issues": issues}
    return {"facts": facts, "priority": priority, **_spend(state, usage),
            "events": state["events"] + [event]}


def after_gate(state: dict) -> str:
    fast = state.get("verdicts", {}).get("fast_candidates", {}).get("value") or []
    if state["request_type"] in ("station", "district") and len(fast) >= 2:
        return "prioritize"
    return "energy_write"


# ── 작성 에이전트 ⇄ 검증 에이전트 ────────────────────────────────

@traceable(name="energy_write_node")
async def energy_write_node(state: dict, deps: SupervisorDeps) -> dict:
    """작성 에이전트 — 사실과 확정 판정으로 답변을 쓰는 노드 (타사면 공개 정보만)"""
    attempt = state.get("rewrite_count", 0) + 1
    if not state.get("facts"):
        draft = "해당 주유소 번호를 찾지 못했습니다. 지도에서 주유소를 다시 선택해 주세요."
        return {"draft": draft, "events": state["events"] + [
            {"type": "DraftWritten", "attempt": attempt, "length": len(draft)}]}
    writer = get_stream_writer()
    verdicts = dict(state.get("verdicts", {}))
    if state.get("priority"):   # 확정 판정은 그대로 두고 작성용으로만 순서·이유를 얹는다
        verdicts["fast_candidates"] = {**verdicts["fast_candidates"],
                                       "value": state["priority"]["order"],
                                       "priority": state["priority"]}
    draft, usage = await deps.quality_llm.write_energy(
        state["request_type"], state["facts"], verdicts,
        state.get("gate_corrections", []), state.get("is_own", False),
        state.get("errors", []), criteria.SITE_CHECK_ITEMS, criteria.GATED_ITEMS,
        replanned_from=state.get("replanned_from"),
        feedback=(state.get("validation") or {}).get("issues"),
        on_token=lambda piece: writer({"type": "AnswerToken", "attempt": attempt,
                                       "delta": piece}))
    return {"draft": draft, **_spend(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


@traceable(name="energy_validate_node")
async def energy_validate_node(state: dict, deps: SupervisorDeps) -> dict:
    """검증 에이전트 — 숫자·인용은 코드로 대조하고, 근거 표기·안내 문구는 LLM-as-Judge가 보는 노드"""
    if not state.get("facts"):
        return {"validation": {"passed": True, "issues": []},
                "events": state["events"] + [{"type": "Validation", "verdict": "생략"}]}
    issues = check_answer(state["draft"], state["facts"], state.get("verdicts", {}))
    judged, usage = deps.fast_llm.judge_answer(state["draft"], state["request_type"],
                                               state.get("is_own", False))
    issues += judged["issues"]
    passed = not issues
    attempt = state.get("rewrite_count", 0) + 1
    verdict = "합격" if passed else ("불합격 → 재작성" if attempt <= MAX_REWRITE
                                     else "불합격 (재작성 한도 도달)")
    return {"validation": {"passed": passed, "issues": issues},
            "rewrite_count": attempt, **_spend(state, usage),
            "events": state["events"] + [{"type": "Validation", "attempt": attempt,
                                          "verdict": verdict, "issues": issues}]}


# ── 상시 작업 (일간 추적 · 주유소 주간 추적) ─────────────────────────────

JOBS = {
    "daily_watch": {"request_type": "watch", "tool": "detect_anomalies", "needed": "alert_needed",
                    "kind": "new_charger_failure", "title": "일간 추적",
                    "quiet": "자사 거점 주변에 새로 멈춘 급속충전기가 없습니다. 알림을 만들지 않았습니다."},
    "weekly": {"request_type": "weekly", "tool": "diff_competitors", "needed": "report_needed",
               "kind": "weekly_competition", "title": "주유소 주간 추적",
               "quiet": "자사 거점 반경 1km에 새로 생기거나 없어진 급속충전기가 없습니다. "
                        "보고를 만들지 않았습니다."},
}


def _job_baseline(job: str, summary: dict) -> dict:
    if job == "daily_watch":
        top = [s["station_id"] for s in summary["stations"][:criteria.ALERT_TOP_STATIONS]]
        return {"alert_needed": summary["alert_needed"], "alert_stations": top}
    stations = []
    for c in sorted(summary["changes"], key=watch_order_key):
        if c["station_id"] not in stations:
            stations.append(c["station_id"])
    return {"report_needed": summary["report_needed"],
            "watch_competitors": stations[:criteria.ALERT_TOP_STATIONS]}


@traceable(name="job_check_node")
async def job_check_node(state: dict, deps: SupervisorDeps) -> dict:
    """상시 작업 1차 감지(코드) — 변화가 없으면 LLM 없이 끝낸다"""
    job, company = state["job"], _company(state)
    args = {"company": company}
    if job == "daily_watch" and state.get("gu"):
        args["gu"] = state["gu"]
    summary = await call_tool(deps, JOBS[job]["tool"], args)
    if "error" in summary:
        return {"tool_results": {"job": summary}, "facts": [], "baseline": {},
                "errors": [{"tool": JOBS[job]["tool"], **summary["error"]}],
                "events": state["events"] + [{"type": "JobChecked", "job": job, "ok": False}]}
    facts = (build_watch_facts(summary, criteria.ALERT_TOP_STATIONS) if job == "daily_watch"
             else build_competition_facts(summary))
    needed = summary[JOBS[job]["needed"]]
    event = {"type": "JobChecked", "job": job, "ok": True, "needed": needed,
             "count": len(summary.get("stations") or summary.get("changes") or [])}
    return {"tool_results": {"job": summary}, "is_own": True, "facts": facts,
            "baseline": _job_baseline(job, summary), "events": state["events"] + [event]}


@traceable(name="policy_node")
async def policy_node(state: dict, deps: SupervisorDeps) -> dict:
    """정책·경쟁 에이전트 — 경쟁 변화 사실만 보고 보고할 변화와 먼저 볼 거점을 판별하는 노드 (fast 모델)"""
    verdicts, usage = deps.fast_llm.judge_competition(state["facts"])
    summary = {item: v.get("value") for item, v in verdicts.items()}
    return {"llm_verdicts": verdicts, **_spend(state, usage),
            "events": state["events"] + [{"type": "Judged", "by": "policy", **summary}]}


@traceable(name="job_deliver_node")
async def job_deliver_node(state: dict, deps: SupervisorDeps) -> dict:
    """상시 작업 결과를 알림함(변화가 있을 때 1건)과 작업 실행 기록에 저장하는 노드"""
    job, company = state["job"], _company(state)
    config = JOBS[job]
    summary = state["tool_results"].get("job", {})
    scope_id = state.get("gu") or company
    alert_id, created = None, False
    if state.get("verdicts", {}).get(config["needed"], {}).get("value"):
        alert_id, created = deps.session_store.save_alert(
            company, job, config["kind"], scope_id, " → ".join(summary.get("window", [])),
            state["draft"], summary)
        final = state["draft"]
    elif "error" in summary:
        final = f"{config['title']} 데이터를 읽지 못했습니다. 수집 상태를 확인해 주세요."
    else:
        window = " → ".join(w[:16].replace("T", " ") for w in summary.get("window", []))
        final = (f"[{company} · {state.get('gu') or '서울 전체'} {config['title']} · {window}]\n"
                 + config["quiet"])
    run_id = deps.session_store.save_job_run(
        job, company, scope_id, state.get("started_at", ""),
        "error" if "error" in summary else "ok", state.get("llm_calls", 0), alert_id)
    event = {"type": "JobSaved", "job": job, "job_run_id": run_id, "alert_id": alert_id,
             "alert_created": created, "llm_calls": state.get("llm_calls", 0)}
    return {"final": final, "alert_id": alert_id, "job_run_id": run_id,
            "events": state["events"] + [event]}


def start_job(state: dict) -> dict:
    """상시 작업 요청을 LLM 분류 없이 코드로 시작하는 함수 (Supervisor 의 작업 분기)"""
    request_type = JOBS[state["job"]]["request_type"]
    event = {"type": "RouteDecided", "route": "job", "request_type": request_type,
             "job": state["job"], "gu": state.get("gu")}
    return {"route": "job", "request_type": request_type,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "events": state.get("events", []) + [event]}


def after_job_check(state: dict) -> str:
    needed = JOBS[state["job"]]["needed"]
    if not state.get("baseline", {}).get(needed):
        return "job_deliver"
    return "analyst" if state["job"] == "daily_watch" else "policy"


# ── 분기 ───────────────────────────────────────────────────────

def after_resolve(state: dict) -> str:
    return "energy_research" if state["route"] == "energy" else "ask_user"


def after_research(state: dict) -> str:
    if (state["request_type"] == "district" and state.get("own_station_count") == 0
            and state.get("replan_count", 0) < MAX_REPLAN):
        return "replan"
    if state.get("is_own") and state.get("baseline"):
        return "analyst"
    return "energy_write"


def after_validate(state: dict) -> str:
    passed = state["validation"]["passed"]
    if not passed and state["rewrite_count"] <= MAX_REWRITE:
        return "energy_write"
    return "job_deliver" if state.get("job") in JOBS else "finalize"
