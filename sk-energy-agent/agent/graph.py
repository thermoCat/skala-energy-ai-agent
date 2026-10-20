"""v2 Supervisor — 실제 langgraph.StateGraph (Wave 4·5·6·7·8·10b·15 통합).

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 gap task 후 재조사) → analysis(needs_analysis=True일 때만
          실제 실행) → write → validate(반려 시 재작성) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.
"""
import asyncio
import json
import time
from functools import partial
from pathlib import Path
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from energy import criteria

from .deps import SupervisorDeps
from .retry import with_retry


def _load_known_systems() -> set[str]:
    """ollama_llm.py/openai_llm.py와 동일한 소스(data/monitoring.json)에서
    시스템 목록을 읽는다 — plan_node가 MCP Prompt에 넘길 system_name을
    쿼리에서 멤버십 매칭으로 뽑아낼 때 쓴다."""
    path = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"
    try:
        return set(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {"auth-service", "web-01"}


KNOWN_SYSTEMS = _load_known_systems()


def _extract_system_name(query: str) -> str:
    """정규식 대신 실제 KNOWN_SYSTEMS 멤버십 매칭 — MCP Prompt(incident_analysis_
    prompt)의 system_name은 "시스템: X" 한 줄짜리 컨텍스트라 항상 정해진 이름
    중 하나로만 고정한다(여러 개 언급돼도 join하지 않음 — 실제 조사 대상 목록은
    이후 plan()의 tasks가 별도로 결정). 쿼리에서 가장 먼저 등장하는 시스템을
    고르고, 하나도 안 걸리면 가장 흔한 사례인 auth-service로 폴백."""
    matches = [(query.index(s), s) for s in KNOWN_SYSTEMS if s in query]
    return min(matches)[1] if matches else "auth-service"


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    # 에너지 — 주유소 핀 클릭 흐름
    company: str                 # 로그인 회사 (예: "SK에너지")
    station_id: str | None       # 지도 핀 클릭 시 프론트가 채움
    gu: str | None
    is_own: bool                 # 자사 거점 여부 (타사면 판별하지 않음)
    tool_results: dict           # Tool 이름 → 응답 (검증 대조용 원본)
    facts: list[dict]            # 번호 붙은 사실 [F1..Fn]
    llm_verdicts: dict           # 분석 에이전트 판별
    baseline: dict               # 코드 기준 판정 (게이트 전용, 분석 에이전트에게 주지 않음)
    verdicts: dict               # 확정 판정 (gate 노드만 씀)
    gate_corrections: list[dict]


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 — 모든 쿼리가 무거운 파이프라인을 탈 필요는 없다."""
    if state.get("station_id"):
        # 지도 핀 클릭은 대상이 이미 정해져 있어 LLM 분류 없이 코드로 보낸다
        event = {"type": "RouteDecided", "route": "station",
                 "station_id": state["station_id"]}
        return {"route": "station", "events": state.get("events", []) + [event]}
    result, usage = deps.fast_llm.route(state["query"])
    event = {"type": "RouteDecided", "route": result["route"]}
    return {"route": result["route"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 direct 경로 — 히스토리 기반 응답 (Wave 12 세션 증명 지점).

    토큰 단위 스트리밍 — get_stream_writer()는 /chat/stream처럼 custom 모드로 소비될
    때만 실제로 전송되고, /chat(ainvoke)처럼 스트리밍하지 않는 호출에서는 자동으로
    no-op이 된다."""
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 4 — Plan/Task 분해. Wave 2의 MCP Prompt가 표준 절차를 깔아준다.

    resource와 마찬가지로 이름을 어느 서버가 들고 있는지 미리 가정하지 않고,
    find_prompt()로 연결된 서버들을 먼저 조회해 찾은 뒤에만 get_prompt를 보낸다."""
    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": _extract_system_name(state["query"]), "symptom": state["query"]},
        server=prompt_server,
    )
    result, usage = deps.fast_llm.plan(state["query"], procedure)
    targets = [t["target"] for t in result["tasks"]
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(result["tasks"]),
             "targets": targets}
    return {"tasks": result["tasks"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 — 권위 게이트. needs_analysis=False인데 섞여 들어온 analysis task를
    프롬프트가 아니라 코드로 강제 제거한다."""
    tasks = state["tasks"]
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed,
            "events": state["events"] + [event]}


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 15 가드 + Wave 10b 병렬 + Wave 9 재시도 + Wave 2 Tool/Resource 실호출."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    # Wave 15 — 객체가 아니라 '실행할 목록'을 검사한다 (not research_agent 버그의 수정판)
    if not research_indices:
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)

    # URI를 코드에 하드코딩하지 않는다. 여러 MCP 서버가 연결돼 있고 그중 어느 서버가
    # "get_incident_runbook" resource를 제공하는지 모른다고 가정하고, 먼저 연결된
    # 서버들을 조회(list)해 유효한 서버를 찾은 뒤에만 그 서버로 요청한다.
    needs_runbook = any(tasks[i]["kind"] != "monitoring" for i in research_indices)
    runbook_server = runbook_uri_template = None
    if needs_runbook:
        runbook_server, runbook_uri_template = await deps.mcp.find_resource_template(
            "get_incident_runbook")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        await asyncio.sleep(0.35)   # 태스크당 고정 지연 — 병렬 효과를 실측하기 위함
        if task["kind"] == "monitoring":
            data, retries = await with_retry(
                deps.mcp.call_tool, "get_monitoring_status",
                {"system_name": task["target"]})
            return {"idx": idx, "source": "monitoring", "data": data, "retries": retries}
        uri = runbook_uri_template.format(incident_type=task["target"])
        data = await deps.mcp.read_resource(uri, server=runbook_server)
        return {"idx": idx, "source": "runbook", "data": data, "retries": 0}

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(i) for i in research_indices))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    for r in results:
        new_tasks[r["idx"]]["status"] = "done"
        collected.append({"source": r["source"], "data": r["data"]})
        retry_total += r["retries"]
        events.append({"type": "TaskCompleted", "id": new_tasks[r["idx"]]["id"],
                       "source": r["source"], "target": new_tasks[r["idx"]]["target"],
                       "retries": r["retries"]})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events}


# 수집된 모니터링 결과(상태·알림 문구)를 보고 어떤 runbook이 맞는지 고른다. LLM에게
# 다시 묻는 대신 코드 레벨 규칙으로 판단한다 — gate(Wave 6)·replan(Wave 7)과 같은 설계
# 원칙: 모델의 판단을 그대로 믿지 않고, 실측 데이터로 검증 가능한 결정은 코드가 내린다.
_INCIDENT_TYPE_RULES = [
    ("latency", "high-latency"),
    ("pool", "high-latency"),
    ("cpu", "cpu-spike"),
    ("memory", "memory-leak"),
    ("heap", "memory-leak"),
    ("timeout", "network-timeout"),
    ("network", "network-timeout"),
    ("disk", "disk-full"),
    ("down", "db-down"),
]


def _classify_incident_type(collected: list[dict]) -> str:
    text = " ".join(
        f"{c['data'].get('status', '')} " + " ".join(c["data"].get("alerts", []))
        for c in collected if c.get("source") == "monitoring"
    ).lower()
    for keyword, incident_type in _INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    return "db-down"   # 매칭되는 증상이 없으면 가장 일반적인 장애 대응 절차로 폴백


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 7 — 1차 수집 결과 평가. runbook이 없으면 gap task만 추가해 재조사."""
    has_runbook = any(c["source"] == "runbook" for c in state.get("collected", []))
    rounds = state.get("replan_rounds", 0)
    if has_runbook or rounds >= 1:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}
    incident_type = _classify_incident_type(state.get("collected", []))
    gap_task = {"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                "target": incident_type, "status": "pending"}
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": gap_task["id"], "target": incident_type}
    return {"tasks": state["tasks"] + [gap_task], "replan_rounds": rounds + 1,
            "events": state["events"] + [verdict]}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 보완 — needs_analysis=True가 정당한 경우(모델의 자기모순이 아니라
    실제로 패턴 비교가 필요하다고 판단한 경우)를 위한 실행 경로. gate가 살려둔
    agent=='analysis' task가 있을 때만 수집된 monitoring 결과들을 비교 분석한다."""
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    summary, usage = deps.fast_llm.analyze(state.get("collected", []))
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "length": len(summary)}
    return {"tasks": new_tasks, "collected": collected,
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 8/10a — 고품질 모델로 초안 작성 (1차는 의도적 저품질).

    토큰마다 attempt 번호를 같이 실어 보낸다 — validate가 반려해 재작성(attempt 증가)
    될 때 프론트가 새 말풍선으로 끊어서 보여줄 수 있게 하기 위함(Wave 8 재작성 루프를
    그대로 노출)."""
    attempt = state.get("validation_attempts", 0) + 1
    writer = get_stream_writer()
    draft, usage = await deps.quality_llm.write(
        state["query"], state.get("collected", []),
        attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer(
            {"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 8 — 코드 기준 검증: 30자 이상 + 절차 번호 포함이어야 합격."""
    draft = state["draft"]
    attempts = state.get("validation_attempts", 0) + 1
    ok = len(draft) >= 30 and "1)" in draft
    verdict = "합격" if ok else "불합격"
    if not ok and attempts >= 3:   # 실모델 무한 재작성 방지 — 최대 3회 후 통과 처리
        ok, verdict = True, "합격(최대 시도 도달 — 통과 처리)"
    feedback = None if ok else (
        f"응답이 너무 짧습니다({len(draft)}자, 최소 30자). 절차를 1) 2) 3) 번호로 "
        f"구체적으로 설명하세요.")
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict}
    return {"validation_attempts": attempts, "validation_feedback": feedback,
            "events": state["events"] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    pipeline = "station-v1" if state.get("route") == "station" else "incident-v2"
    return {"final": state["draft"],
            "events": state["events"] + [{"type": "Finalize", "pipeline": pipeline}]}


# ── 에너지: 주유소 핀 클릭 흐름 ────────────────────────────────────────
# 조회(코드) → 분석 에이전트 판별(LLM) → 권위 게이트(코드 대조·교정) → 작성(LLM)


def _fact(facts: list[dict], key: str, label: str, value, unit: str, source: str) -> None:
    facts.append({"id": f"F{len(facts) + 1}", "key": key, "label": label,
                  "value": value, "unit": unit, "source": source})


def build_station_facts(results: dict) -> list[dict]:
    """Tool 응답에서 번호 붙은 사실 목록 [F1..Fn]을 만드는 함수

    Args:
        results: Tool 이름 → 응답 dict (실패한 Tool은 error 포함)
    Returns:
        {"id", "key", "label", "value", "unit", "source"} 목록. 실패한 Tool의 사실은 빠짐
    """
    facts: list[dict] = []
    card = results.get("get_station_card", {})
    if "error" not in card:
        _fact(facts, "station", "거점",
              f"{card['gu']} · {card['brand']} {card['name']} ({card['facility']})",
              "", card["source"])
        _fact(facts, "in_station_fast_ids", "주유소 안 급속충전기 ID (반경 150m)",
              card["in_station_fast_ids"], "개", card["source"])
    demand = results.get("get_fuel_demand", {})
    if "error" not in demand:
        ev, diesel = demand["fuels"]["전기"], demand["fuels"]["경유"]
        months = "→".join(demand["months"])
        _fact(facts, "ev_growth_pct", f"{demand['gu']} 전기차 1년 증감률 ({months})",
              ev["rate_pct"], "%", demand["source"])
        _fact(facts, "seoul_ev_growth_pct", f"서울 전기차 1년 증감률 ({months})",
              ev["seoul_rate_pct"], "%", demand["source"])
        _fact(facts, "diesel_rate_pct", f"{demand['gu']} 경유차 1년 증감률 ({months})",
              diesel["rate_pct"], "%", demand["source"])
        _fact(facts, "seoul_diesel_rate_pct", f"서울 경유차 1년 증감률 ({months})",
              diesel["seoul_rate_pct"], "%", demand["source"])
    supply = results.get("get_ev_supply", {})
    if "error" not in supply:
        _fact(facts, "fast_per_10k_ev", f"{supply['gu']} 전기차 1만 대당 급속 ID",
              supply["fast_per_10k_ev"], "ID", supply["source"])
        _fact(facts, "seoul_fast_per_10k_ev", "서울 전기차 1만 대당 급속 ID",
              supply["seoul_fast_per_10k_ev"], "ID", supply["source"])
    return facts


async def _call_tool_safely(deps: SupervisorDeps, name: str, args: dict) -> dict:
    # 한 Tool이 예외로 죽어도 나머지 사실로 답하도록 error dict로 바꿔 돌려준다 (장애 격리)
    try:
        data, _ = await with_retry(deps.mcp.call_tool, name, args)
        return data
    except Exception as exc:
        return {"error": {"message": type(exc).__name__}}


@traceable(name="station_research_node")
async def station_research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """주유소 카드를 받고 그 자치구의 수요·공급·기준 판정을 병렬로 조회하는 노드"""
    events = list(state["events"])
    card = await _call_tool_safely(deps, "get_station_card",
                                   {"station_id": state["station_id"]})
    events.append({"type": "TaskCompleted", "source": "get_station_card",
                   "target": state["station_id"], "ok": "error" not in card})
    if "error" in card:
        return {"tool_results": {"get_station_card": card}, "facts": [], "is_own": False,
                "events": events}

    gu = card["gu"]
    is_own = card["brand"] == state.get("company", "SK에너지")
    names = ["get_fuel_demand", "get_ev_supply"]
    if is_own:   # 타사 거점은 판별하지 않으므로 기준 판정도 부르지 않는다
        names.append("score_district")
    started = time.perf_counter()
    responses = await asyncio.gather(
        *(_call_tool_safely(deps, n, {"gu": gu}) for n in names))
    elapsed = round(time.perf_counter() - started, 3)
    results = {"get_station_card": card, **dict(zip(names, responses))}
    for name in names:
        events.append({"type": "TaskCompleted", "source": name, "target": gu,
                       "ok": "error" not in results[name]})

    baseline = results.pop("score_district", {})
    facts = build_station_facts(results)
    events.append({"type": "FactsCollected", "count": len(facts), "is_own": is_own})
    return {"gu": gu, "is_own": is_own, "tool_results": results, "facts": facts,
            "baseline": baseline, "parallel_elapsed": elapsed, "events": events}


@traceable(name="analyst_node")
async def analyst_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """분석 에이전트 — 사실 [F]만 보고 판별하는 노드 (기준 판정은 넘기지 않음)"""
    verdicts, usage = deps.quality_llm.judge_station(state["facts"])
    summary = {item: v.get("value") for item, v in verdicts.items()}
    return {"llm_verdicts": verdicts, "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Judged", "by": "analyst", **summary}]}


@traceable(name="authority_gate_node")
async def authority_gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """권위 게이트 — 분석 에이전트 판별을 코드 기준과 대조해 확정 판정을 만드는 노드

    기준과 다르면 코드 기준으로 교정하고 GateCorrected 이벤트를 남긴다.
    판별·기준·교정 여부는 decision_log 에 저장한다.
    """
    llm, baseline = state.get("llm_verdicts", {}), state.get("baseline", {})
    events = list(state["events"])
    verdicts, corrections = {}, []
    for n, item in enumerate(criteria.GATED_ITEMS, start=1):
        llm_value = llm.get(item, {}).get("value")
        base_value = baseline.get(item)
        final_value = llm_value
        if base_value is not None and llm_value != base_value:
            final_value = base_value
            corrections.append({"item": item, "llm": llm_value, "baseline": base_value})
            events.append({"type": "GateCorrected", "item": item,
                           "label": criteria.GATED_ITEMS[item],
                           "llm": llm_value, "baseline": base_value})
        verdicts[item] = {"id": f"J{n}", "value": final_value,
                          "facts": llm.get(item, {}).get("facts", [])}

    snapshot = state["tool_results"]["get_station_card"].get("snapshot", "")
    deps.session_store.log_decision(
        company=state.get("company", "SK에너지"), scope_type="station",
        scope_id=state["station_id"], snapshot=snapshot,
        rule_version=baseline.get("rule_version", ""), llm_verdict=llm,
        baseline=baseline, verdict=verdicts, corrections=corrections)
    events.append({"type": "Judged", "by": "gate", "corrected": len(corrections),
                   **{item: v["value"] for item, v in verdicts.items()}})
    return {"verdicts": verdicts, "gate_corrections": corrections, "events": events}


@traceable(name="station_write_node")
async def station_write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """작성 에이전트 — 사실과 확정 판정으로 답변을 쓰는 노드 (타사면 공개 정보만)"""
    if not state.get("facts"):
        draft = "해당 주유소 번호를 찾지 못했습니다. 지도에서 주유소를 다시 선택해 주세요."
        return {"draft": draft,
                "events": state["events"] + [{"type": "DraftWritten", "length": len(draft)}]}
    writer = get_stream_writer()
    draft, usage = await deps.quality_llm.write_station(
        state["facts"], state.get("verdicts", {}), state.get("gate_corrections", []),
        state.get("is_own", False), criteria.SITE_CHECK_ITEMS, criteria.GATED_ITEMS,
        on_token=lambda piece: writer({"type": "AnswerToken", "attempt": 1, "delta": piece}))
    return {"draft": draft, "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": 1,
                                          "length": len(draft)}]}


def _after_route(state: AgentState) -> str:
    return {"chat": "chat_answer", "station": "station_research"}.get(state["route"], "plan")


def _after_station_research(state: AgentState) -> str:
    # 타사 거점이나 없는 번호는 판별하지 않고 바로 작성 (타사 투자 판별 금지)
    return "analyst" if state.get("is_own") and state.get("facts") else "station_write"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # Wave 13 — 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))
    g.add_node("station_research", node(station_research_node))
    g.add_node("analyst", node(analyst_node))
    g.add_node("authority_gate", node(authority_gate_node))
    g.add_node("station_write", node(station_write_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan",
                             "station_research": "station_research"})
    g.add_conditional_edges("station_research", _after_station_research,
                            {"analyst": "analyst", "station_write": "station_write"})
    g.add_edge("analyst", "authority_gate")
    g.add_edge("authority_gate", "station_write")
    g.add_edge("station_write", "finalize")
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
