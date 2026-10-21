"""실제 LLM(Ollama·OpenAI)이 쓰는 에너지 프롬프트와, 모델 출력을 코드로 다듬는 함수

모델 출력은 그대로 믿지 않는다 — 형식이 깨지거나 허용 밖 값이 오면 여기서 버리거나 고친다.
판별 값이 틀린 것은 여기서 고치지 않고 권위 게이트가 기준으로 교정한다.
"""
import json
import re

from .answer_template import EnergyAnswer

# ── Supervisor 분류 ──────────────────────────────────────────────

CLASSIFY_SYSTEM = (
    "당신은 주유소 복합 에너지 허브 서비스의 Supervisor 분류기다. 사용자 질문에서 아래 값만 "
    "뽑아 JSON 객체로 답하라. 질문에 없으면 null.\n"
    '{"gu": 서울 자치구 이름, "station_name": "…주유소" 또는 "…충전소"로 끝나는 상호, '
    '"brand": 회사 이름, "fast_add": 새로 넣을 충전기 수 — "충전기 N대 신설"·"급속 N기"의 N(정수), "kw": 출력 kW(정수)}'
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "gu": {"type": ["string", "null"]},
        "station_name": {"type": ["string", "null"]},
        "brand": {"type": ["string", "null"]},
        "fast_add": {"type": ["integer", "null"]},
        "kw": {"type": ["integer", "null"]},
    },
    "required": ["gu", "station_name", "brand", "fast_add", "kw"],
}


def classify_user(query: str, gu_names: tuple, brand_names: tuple) -> str:
    """분류 프롬프트의 사용자 메시지를 만드는 함수"""
    return (f"## 허용 자치구\n{', '.join(gu_names)}\n\n## 허용 회사 표현\n{', '.join(brand_names)}"
            f"\n\n## 질문\n{query}")


# 상호처럼 보이는 단어 ("박미주유소", "(주)홍릉가스충전소") — 시설 종류를 가리키는 일반 단어는 뺀다
STATION_WORD = re.compile(r"([가-힣A-Za-z0-9()㈜\-]+(?:주유소|충전소))")
GENERIC_FACILITY_WORDS = {"수소충전소", "LPG충전소", "급속충전소", "전기충전소", "충전소", "주유소"}


def station_word(query: str) -> str | None:
    """질문에서 상호처럼 보이는 단어를 찾는 함수 (모델이 놓쳤을 때 코드가 보충)

    Args:
        query: 사용자 질문
    Returns:
        "…주유소" / "…충전소" 단어, 없으면 None
    """
    for word in STATION_WORD.findall(query):
        if word not in GENERIC_FACILITY_WORDS:
            return word
    return None


def normalize_classify(content: str, query: str, gu_names: tuple, brand_aliases: dict) -> dict:
    """분류 출력에서 허용 값만 남기는 함수 (질문에 실제로 없는 값은 버림)

    Args:
        content: 모델 출력 (JSON 문자열)
        query: 사용자 질문
        gu_names: 허용 자치구
        brand_aliases: 회사 표현 → 데이터 회사 이름
    Returns:
        {"gu", "station_name", "brand", "fast_add", "kw"}
    """
    raw = _load_json(content)
    gu = raw.get("gu") if raw.get("gu") in gu_names and raw.get("gu") in query else None
    gu = gu or next((g for g in sorted(gu_names, key=len, reverse=True) if g in query), None)
    name = raw.get("station_name")
    station_name = name if isinstance(name, str) and name in query and \
        re.search(r"(주유소|충전소)$", name) and len(name) > 3 else None
    # 소형 모델이 상호 대신 회사명("S-OIL")만 뽑는 경우가 있어 질문에서 직접 보충한다
    station_name = station_name or station_word(query)
    brand = raw.get("brand")
    brand = brand_aliases.get(brand) if isinstance(brand, str) and brand in query else None
    # 급속 수·출력은 숫자를 지어낼 수 있어 질문의 숫자와 맞을 때만 쓴다
    fast = re.search(r"(?:급속|충전기)\s*(\d+)\s*(?:기|대)", query)
    fast_add = int(fast.group(1)) if fast and raw.get("fast_add") == int(fast.group(1)) else None
    kw = re.search(r"(\d+)\s*kW", query, re.IGNORECASE)
    return {"gu": gu, "station_name": station_name, "brand": brand, "fast_add": fast_add,
            "kw": int(kw.group(1)) if kw else None}


# ── 분석 에이전트 판별 ────────────────────────────────────────────

JUDGE_SYSTEM = (
    "당신은 정유사 리테일 네트워크의 분석 에이전트다. 아래 사실 [F]만 보고 판별하라. "
    "사실에 없는 숫자나 정보(판매량·부지·전력)는 쓰지 마라. 모든 판별에 근거 사실 번호를 붙이고, "
    "reason 은 한국어 한두 문장으로 쓴다.\n"
    "순위 읽는 법: 모든 순위는 서울 25개 자치구 중 순위이고 1위가 가장 심한 쪽이다. "
    "25구의 20%는 5구이므로 '상위 20%'는 1~5위(동점 포함)다. 서울 평균과의 비교보다 25구 중 순위를 우선한다.\n"
    "- ev_bottleneck: 전기차 증가가 서울에서 가장 빠른 편이면서 동시에 전기차 대비 급속이 가장 부족한 편이라, "
    "두 순위를 함께 보았을 때 25구 중 가장 심한 편(대략 상위 20%)인 '전기 병목'인가. "
    "한쪽만 심하고 다른 쪽이 중간 이하이면 병목이 아니다\n"
    "- diesel_idle / lpg_idle: 그 연료 감소율 순위가 상위 20%(1~5위)라 설비 유휴 위험이 있는가\n"
    "- fast_candidates: ev_bottleneck 이 true 일 때만, 묶음의 주유소(LPG 충전소 제외) 중 주유소 안 급속이 없고 "
    "반경 1km 급속이 묶음 중앙값 이하로 적은 곳을 적은 순으로 최대 2곳 station_id로. 병목이 아니면 빈 목록\n"
    "JSON 객체로만 답하라: "
    '{"ev_bottleneck": {"value": bool, "facts": ["F1"], "reason": str}, '
    '"diesel_idle": {...}, "lpg_idle": {...}, '
    '"fast_candidates": {"value": [station_id], "facts": [...], "reason": str, '
    '"candidate_reasons": [{"station_id": str, "facts": [...], "text": str}]}}'
)

SCENARIO_JUDGE_SYSTEM = (
    "당신은 정유사 투자 검토를 돕는 분석 에이전트다. 급속충전기 추가 시나리오 계산 사실 [F]만 보고 "
    "추가 전·후에 이 자치구가 '전기 병목'(병목 점수 순위가 25구 중 상위 5위 안)인지 판별하라. "
    "JSON 객체로만 답하라: "
    '{"ev_bottleneck_before": {"value": bool, "facts": ["F1"], "reason": str}, '
    '"ev_bottleneck_after": {"value": bool, "facts": [...], "reason": str}}'
)

WATCH_JUDGE_SYSTEM = (
    "당신은 정유사 리테일 네트워크의 매일 감시 분석 에이전트다. 사실 [F]만 보고 판별하라.\n"
    "- alert_needed: 오늘 관리자에게 알림을 보내야 하는가 (자사 거점 주변에 새로 멈춘 급속이 있는가)\n"
    "- alert_stations: 알림 맨 위에 올릴 자사 거점 station_id 최대 3곳, 먼저 볼 순서대로\n"
    "JSON 객체로만 답하라: "
    '{"alert_needed": {"value": bool, "facts": ["F1"], "reason": str}, '
    '"alert_stations": {"value": [station_id], "facts": [...], "reason": str}}'
)

COMPETITION_JUDGE_SYSTEM = (
    "당신은 정유사의 정책·경쟁 에이전트다. 자사 거점 주변 급속충전기 신설·철거 사실 [F]만 보고 판별하라.\n"
    "- report_needed: 이번 주 관리자에게 보고할 경쟁 변화가 있는가\n"
    "- watch_competitors: 먼저 볼 자사 거점 station_id 최대 3곳 — '일반 이용' 타사 신설이 있는 거점을 먼저, "
    "그다음 가까운 곳부터 ('버스 전용'·'이용 제한' 충전기는 주유소 고객과 수요가 겹치지 않음)\n"
    "JSON 객체로만 답하라: "
    '{"report_needed": {"value": bool, "facts": ["F1"], "reason": str}, '
    '"watch_competitors": {"value": [station_id], "facts": [...], "reason": str}}'
)

_VERDICT = {"type": "object",
            "properties": {"value": {"type": "boolean"},
                           "facts": {"type": "array", "items": {"type": "string"}},
                           "reason": {"type": "string"}},
            "required": ["value", "facts", "reason"]}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "ev_bottleneck": _VERDICT, "diesel_idle": _VERDICT, "lpg_idle": _VERDICT,
        "fast_candidates": {
            "type": "object",
            "properties": {
                "value": {"type": "array", "items": {"type": "string"}},
                "facts": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
                "candidate_reasons": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"station_id": {"type": "string"},
                                   "facts": {"type": "array", "items": {"type": "string"}},
                                   "text": {"type": "string"}},
                    "required": ["station_id", "facts", "text"]}},
            },
            "required": ["value", "facts", "reason", "candidate_reasons"],
        },
    },
    "required": ["ev_bottleneck", "diesel_idle", "lpg_idle", "fast_candidates"],
}

SCENARIO_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"ev_bottleneck_before": _VERDICT, "ev_bottleneck_after": _VERDICT},
    "required": ["ev_bottleneck_before", "ev_bottleneck_after"],
}

WATCH_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_needed": _VERDICT,
        "alert_stations": {"type": "object",
                           "properties": {"value": {"type": "array", "items": {"type": "string"}},
                                          "facts": {"type": "array", "items": {"type": "string"}},
                                          "reason": {"type": "string"}},
                           "required": ["value", "facts", "reason"]},
    },
    "required": ["alert_needed", "alert_stations"],
}

COMPETITION_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"report_needed": _VERDICT,
                   "watch_competitors": WATCH_JUDGE_SCHEMA["properties"]["alert_stations"]},
    "required": ["report_needed", "watch_competitors"],
}

SCENARIO_ITEMS = ("ev_bottleneck_before", "ev_bottleneck_after")
DISTRICT_ITEMS = ("ev_bottleneck", "diesel_idle", "lpg_idle")


def render_facts(facts: list[dict]) -> str:
    """사실 목록을 프롬프트용 텍스트로 바꾸는 함수 ("[F3] 이름: 값 단위" 한 줄씩)"""
    lines = []
    for f in facts:
        tag = f" (station_id={f['station_id']})" if f.get("station_id") else ""
        lines.append(f"[{f['id']}] {f['label']}: {f['value']} {f['unit']}{tag}".rstrip())
    return "\n".join(lines)


def judge_prompt(request_type: str, facts: list[dict]) -> tuple[str, str, dict]:
    """판별 프롬프트(system, user)와 출력 스키마를 고르는 함수"""
    if request_type == "scenario":
        return SCENARIO_JUDGE_SYSTEM, render_facts(facts), SCENARIO_JUDGE_SCHEMA
    if request_type == "watch":
        return WATCH_JUDGE_SYSTEM, render_facts(facts), WATCH_JUDGE_SCHEMA
    if request_type == "weekly":
        return COMPETITION_JUDGE_SYSTEM, render_facts(facts), COMPETITION_JUDGE_SCHEMA
    return JUDGE_SYSTEM, render_facts(facts), JUDGE_SCHEMA


def _clean_verdict(raw, known_ids: set) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    value = raw.get("value") if isinstance(raw.get("value"), bool) else None
    facts = [f for f in raw.get("facts", []) if f in known_ids] if isinstance(
        raw.get("facts"), list) else []
    return {"value": value, "facts": facts, "reason": str(raw.get("reason") or "")}


def normalize_judgement(content: str, request_type: str, facts: list[dict]) -> dict:
    """판별 출력의 형식만 다듬는 함수 — 없는 사실 번호·묶음 밖 거점은 버린다 (값 교정은 게이트 몫)

    Args:
        content: 모델 출력 (JSON 문자열)
        request_type: "station" | "district" | "scenario"
        facts: 번호 붙은 사실 목록
    Returns:
        항목별 {"value", "facts", "reason"} 판별 dict
    """
    raw = _load_json(content)
    known = {f["id"] for f in facts}
    if request_type == "watch":
        stations = {f["station_id"] for f in facts if f["key"].startswith("new_failures:")}
        top = raw.get("alert_stations") if isinstance(raw.get("alert_stations"), dict) else {}
        chosen = [s for s in top.get("value", []) or [] if s in stations][:3]
        return {"alert_needed": _clean_verdict(raw.get("alert_needed"), known),
                "alert_stations": {**_clean_verdict(top, known), "value": chosen, "reasons": {}}}
    if request_type == "weekly":
        stations = {f["station_id"] for f in facts if f["key"].startswith("distance:")}
        top = raw.get("watch_competitors") if isinstance(raw.get("watch_competitors"), dict) else {}
        chosen = [s for s in top.get("value", []) or [] if s in stations][:3]
        return {"report_needed": _clean_verdict(raw.get("report_needed"), known),
                "watch_competitors": {**_clean_verdict(top, known), "value": chosen, "reasons": {}}}
    if request_type == "scenario":
        return {item: _clean_verdict(raw.get(item), known) for item in SCENARIO_ITEMS}

    result = {item: _clean_verdict(raw.get(item), known) for item in DISTRICT_ITEMS}
    cluster_ids = {f["station_id"] for f in facts
                   if f["key"].startswith("fast_1km:") and f.get("prodcd") == "B027"}
    fc = raw.get("fast_candidates") if isinstance(raw.get("fast_candidates"), dict) else {}
    chosen = [s for s in fc.get("value", []) if s in cluster_ids][:2] \
        if isinstance(fc.get("value"), list) else []
    reasons = {r["station_id"]: {"facts": [x for x in r.get("facts", []) if x in known],
                                 "text": str(r.get("text") or "")}
               for r in fc.get("candidate_reasons", []) or []
               if isinstance(r, dict) and r.get("station_id") in chosen}
    result["fast_candidates"] = {"value": chosen,
                                 "facts": [x for x in fc.get("facts", []) or [] if x in known],
                                 "reason": str(fc.get("reason") or ""), "reasons": reasons}
    return result


# ── 분석 에이전트: 어디부터, 왜 ─────────────────────────────────────

PRIORITY_SYSTEM = (
    "당신은 정유사 투자 검토를 돕는 분석 에이전트다. 아래 급속 후보 거점들은 이미 기준으로 자격이 확정됐다. "
    "어느 거점부터 현장 검토할지 순서와 이유를 정하라. 정답 규칙은 없다 — 사실을 저울질하는 것이 당신의 일이다.\n"
    "고려할 신호: 반경 1km 급속 수(적을수록 새 수요를 흡수할 여지), 충전 중 비율(높을수록 수요 확인, 한 시점 대리 지표), "
    "주변 새로 멈춘 급속(대체 수요가 생김), 타사 신설(경쟁이 세짐), 지난 사람 결정(보류면 사유 해소 전엔 뒤로).\n"
    "규칙: 후보 station_id 만 쓰고 모두 한 번씩 순서에 넣는다. 거점마다 이유 한두 문장을 한국어로 쓰고, "
    "숫자는 사실 값 그대로 쓰며 숫자 바로 뒤에 [F번호]를 붙인다. 그 거점의 사실 번호를 facts 에 넣는다. "
    "판매량·부지·전력처럼 사실에 없는 정보는 쓰지 않는다.\n"
    'JSON 객체로만 답하라: {"order": [station_id], "reasons": [{"station_id": str, "text": str, '
    '"facts": ["F1"]}], "summary": str}'
)

PRIORITY_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "string"}},
        "reasons": {"type": "array", "items": {
            "type": "object",
            "properties": {"station_id": {"type": "string"}, "text": {"type": "string"},
                           "facts": {"type": "array", "items": {"type": "string"}}},
            "required": ["station_id", "text", "facts"]}},
        "summary": {"type": "string"},
    },
    "required": ["order", "reasons", "summary"],
}


def priority_user(facts: list[dict], candidates: list[str]) -> str:
    """우선순위 판단 프롬프트의 사용자 메시지를 만드는 함수"""
    names = {f["station_id"]: f.get("name", f["station_id"]) for f in facts if f.get("station_id")}
    listed = "\n".join(f"- {sid} {names.get(sid, '')}" for sid in candidates)
    return f"## 확정된 급속 후보\n{listed}\n\n## 후보 거점 사실\n{render_facts(facts)}"


def normalize_priority(content: str, candidates: list[str]) -> dict:
    """우선순위 출력의 형식만 다듬는 함수 (순서·이유를 고치지는 않음 — 검사는 check_priority)"""
    raw = _load_json(content)
    order = [s for s in dict.fromkeys(raw.get("order") or []) if isinstance(s, str)]
    reasons = {}
    for r in raw.get("reasons") or []:
        if isinstance(r, dict) and r.get("station_id") in candidates:
            reasons[r["station_id"]] = {"text": str(r.get("text") or "").strip(),
                                        "facts": [x for x in r.get("facts") or []
                                                  if isinstance(x, str)]}
    return {"order": order, "reasons": reasons, "summary": str(raw.get("summary") or "")}


# ── 작성 에이전트 ────────────────────────────────────────────────

WRITE_SYSTEM = (
    "당신은 정유사 리테일 네트워크 관리자에게 보고하는 작성 에이전트다. 한국어로 짧게 쓴다.\n"
    "'답변 뼈대'를 읽기 쉬운 보고 문장으로 다듬어라. 규칙:\n"
    "1. 뼈대의 줄 순서, 모든 숫자, [F번호]·[J번호]를 그대로 남긴다. 숫자를 빼거나 바꾸거나 새로 만들지 않는다\n"
    "2. 결론(충전기 부족·차량 감소·급속 후보)은 뼈대와 같아야 한다\n"
    "3. 판매량·부지·전력처럼 사실에 없는 정보는 말하지 않는다\n"
    "4. 뼈대의 '현장 확인 항목' 줄과 마지막 안내 문장은 글자 그대로 둔다\n"
    "5. 뼈대에 없는 줄·제목·항목(예: 새 '현장 확인 항목', '안내')을 덧붙이지 않는다\n"
    "답변만 출력하라."
)


def write_prompt(request_type: str, facts: list[dict], verdicts: dict, corrections: list[dict],
                 is_own: bool, errors: list[dict], site_checks: tuple, labels: dict,
                 replanned_from: str | None, feedback: list[str] | None) -> tuple[str, str, list[str]]:
    """작성 프롬프트(system, user)와 답변 뼈대를 만드는 함수 — 코드가 만든 뼈대 + 사실 원문"""
    skeleton = EnergyAnswer(facts, verdicts).build(
        request_type, corrections, is_own, errors, site_checks, labels, replanned_from)
    parts = ["## 답변 뼈대\n" + "\n".join(skeleton),
             f"## 사실 원문 (참고용)\n{render_facts(facts)}"]
    if feedback:
        parts.append("## 검증 반려 사유 (반드시 고칠 것)\n" + "\n".join(f"- {x}" for x in feedback))
    return WRITE_SYSTEM, "\n\n".join(parts), skeleton


def trim_to_skeleton(draft: str, skeleton: list[str]) -> str:
    """작성 결과에서 뼈대 마지막 줄 뒤에 모델이 덧붙인 내용을 잘라내는 함수

    Args:
        draft: 작성 에이전트 출력
        skeleton: 코드가 만든 답변 뼈대 줄 목록
    Returns:
        뼈대 마지막 줄까지만 남긴 답변 (마지막 줄을 못 찾으면 그대로)
    """
    # 실모델이 프롬프트 규칙을 어기고 근거 없는 "현장 확인 항목"·"안내"를 덧붙인 일이 있었다
    last = skeleton[-1].strip()
    cut = draft.find(last)
    return draft[:cut + len(last)].rstrip() if cut >= 0 else draft


# ── 검증 에이전트 LLM-as-Judge ─────────────────────────────────────

ANSWER_JUDGE_SYSTEM = (
    "당신은 검증 에이전트다. 답변이 아래 기준을 지켰는지만 본다. 숫자 정확성은 코드가 따로 본다.\n"
    "1. 숫자마다 [F번호] 근거 표기가 있는가\n"
    "2. (자사 진단일 때) '현장 확인 항목'이 있는가\n"
    "3. (자사 진단일 때) 투자 결정이 아니라 진단이라는 문장이 있는가\n"
    "4. 판매량·부지·전력처럼 근거 없는 정보를 단정하지 않았는가\n"
    "모두 지켰으면 issues 는 빈 목록이다. 지키지 않은 기준이 있을 때만 그 내용을 짧게 적는다.\n"
    'JSON 객체로만 답하라. 예: {"issues": []}'
)

ANSWER_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {"type": "string"}}},
    "required": ["issues"],
}


def answer_judge_user(draft: str, request_type: str, is_own: bool) -> str:
    """답변 판정 프롬프트의 사용자 메시지를 만드는 함수"""
    kind = "자사 진단" if is_own and request_type in ("station", "district", "scenario") \
        else "공개 정보 안내"
    return f"## 답변 종류\n{kind}\n\n## 답변\n{draft}"


def normalize_answer_judgement(content: str) -> dict:
    """답변 판정 출력을 {"passed", "issues"}로 다듬는 함수 (형식이 깨지면 판정 보류 = 통과)"""
    raw = _load_json(content)
    issues = [str(x).strip() for x in raw.get("issues", [])] \
        if isinstance(raw.get("issues"), list) else []
    # 소형 모델은 프롬프트 문구를 그대로 지적으로 베끼기도 해서 그런 줄은 버린다
    issues = [x for x in issues if x and x not in ANSWER_JUDGE_SYSTEM]
    return {"passed": not issues, "issues": issues}


def _load_json(content: str) -> dict:
    # 일부 모델이 JSON 앞뒤에 설명이나 ```json 울타리를 붙여서 가장 바깥 {...}만 읽는다
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}
