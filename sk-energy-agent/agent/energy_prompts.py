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
    '"brand": 회사 이름, "fast_add": "급속 N기"의 N(정수), "kw": 출력 kW(정수)}'
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
    name = raw.get("station_name")
    station_name = name if isinstance(name, str) and name in query and \
        re.search(r"(주유소|충전소)$", name) and len(name) > 3 else None
    brand = raw.get("brand")
    brand = brand_aliases.get(brand) if isinstance(brand, str) and brand in query else None
    # 급속 수·출력은 숫자를 지어낼 수 있어 질문의 숫자와 맞을 때만 쓴다
    fast = re.search(r"급속\s*(\d+)\s*기", query)
    fast_add = int(fast.group(1)) if fast and raw.get("fast_add") == int(fast.group(1)) else None
    kw = re.search(r"(\d+)\s*kW", query, re.IGNORECASE)
    return {"gu": gu, "station_name": station_name, "brand": brand, "fast_add": fast_add,
            "kw": int(kw.group(1)) if kw else None}


# ── 분석 에이전트 판별 ────────────────────────────────────────────

JUDGE_SYSTEM = (
    "당신은 정유사 리테일 네트워크의 분석 에이전트다. 아래 사실 [F]만 보고 판별하라. "
    "사실에 없는 숫자나 정보(판매량·부지·전력)는 쓰지 마라. 모든 판별에 근거 사실 번호를 붙여라.\n"
    "- ev_bottleneck: 이 자치구가 전기차 증가는 빠른데 급속충전 공급은 부족한 '전기 병목'인가\n"
    "- diesel_idle / lpg_idle: 그 연료 차량이 25개 자치구 중 가장 많이 줄어든 편(대략 상위 20%)이라 "
    "설비 유휴 위험이 있는가 — 감소율 순위 사실을 보고 판단하라\n"
    "- fast_candidates: 전기 병목이라면, 묶음의 주유소(LPG 충전소 제외) 중 주유소 안 급속이 없고 "
    "반경 1km 급속이 적은 곳을 우선순위 순서로 최대 2곳 station_id로. 병목이 아니면 빈 목록\n"
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


# ── 작성 에이전트 ────────────────────────────────────────────────

WRITE_SYSTEM = (
    "당신은 정유사 리테일 네트워크 관리자에게 보고하는 작성 에이전트다. 한국어로 짧게 쓴다.\n"
    "'답변 뼈대'를 읽기 쉬운 보고 문장으로 다듬어라. 규칙:\n"
    "1. 뼈대의 줄 순서, 모든 숫자, [F번호]·[J번호]를 그대로 남긴다. 숫자를 빼거나 바꾸거나 새로 만들지 않는다\n"
    "2. 결론(병목·유휴 위험·급속 후보)은 뼈대와 같아야 한다\n"
    "3. 판매량·부지·전력처럼 사실에 없는 정보는 말하지 않는다\n"
    "4. 뼈대의 '현장 확인 항목' 줄과 마지막 안내 문장은 글자 그대로 둔다\n"
    "답변만 출력하라."
)


def write_prompt(request_type: str, facts: list[dict], verdicts: dict, corrections: list[dict],
                 is_own: bool, errors: list[dict], site_checks: tuple, labels: dict,
                 replanned_from: str | None, feedback: list[str] | None) -> tuple[str, str]:
    """작성 프롬프트(system, user)를 만드는 함수 — 코드가 만든 답변 뼈대 + 사실 원문"""
    skeleton = EnergyAnswer(facts, verdicts).build(
        request_type, corrections, is_own, errors, site_checks, labels, replanned_from)
    parts = ["## 답변 뼈대\n" + "\n".join(skeleton),
             f"## 사실 원문 (참고용)\n{render_facts(facts)}"]
    if feedback:
        parts.append("## 검증 반려 사유 (반드시 고칠 것)\n" + "\n".join(f"- {x}" for x in feedback))
    return WRITE_SYSTEM, "\n\n".join(parts)


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
