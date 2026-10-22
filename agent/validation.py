"""답변 숫자를 사실 [F]와 코드로 대조하는 검증 함수 (검증 에이전트의 코드 부분)"""
import re

NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
CITATION = re.compile(r"\[([FJ]\d+)\]")
STATION_ID = re.compile(r"\b[A-Z]\d{7,}\b")   # 오피넷 주유소 번호 (예: A0009172)
LIST_MARKER = re.compile(r"(?m)^\s*\d+\)")
TOLERANCE = 0.051   # 소수 첫째 자리 반올림 차이까지 허용


def allowed_numbers(facts: list[dict], extra: tuple = ()) -> list[float]:
    """답변에 써도 되는 숫자 목록을 만드는 함수

    사실 값과, 사실 이름·글자 값 안에 들어 있는 숫자(기준월, "반경 1km" 등)를 허용한다.

    Args:
        facts: 번호 붙은 사실 목록
        extra: 판정에서 나온 숫자 (예: 확정 후보 수)
    Returns:
        허용 숫자 목록
    """
    numbers = [float(x) for x in extra]
    for f in facts:
        if isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool):
            numbers.append(float(f["value"]))
        texts = [f["label"]] + ([f["value"]] if isinstance(f["value"], str) else [])
        for text in texts:
            numbers += [float(n) for n in NUMBER.findall(text)]
    return numbers


def _strip_known_text(draft: str, facts: list[dict]) -> str:
    text = CITATION.sub("", draft)
    text = STATION_ID.sub("", text)
    text = LIST_MARKER.sub("", text)
    # 상호에 숫자가 들어 있는 경우가 있어("재정제2주유소") 거점 이름은 빼고 본다
    names = {f["name"] for f in facts if f.get("name")}
    names |= {f["value"] for f in facts if isinstance(f["value"], str)}
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, "")
    return text


def unbacked_numbers(draft: str, facts: list[dict], extra: tuple = ()) -> list[str]:
    """사실로 뒷받침되지 않는 숫자를 찾는 함수

    Args:
        draft: 답변 초안
        facts: 번호 붙은 사실 목록
        extra: 판정에서 나온 숫자
    Returns:
        근거를 찾지 못한 숫자 문자열 목록
    """
    allowed = allowed_numbers(facts, extra)
    found = NUMBER.findall(_strip_known_text(draft, facts))
    return [n for n in found
            if not any(abs(abs(float(n)) - abs(a)) < TOLERANCE for a in allowed)]


def unknown_citations(draft: str, facts: list[dict], verdicts: dict) -> list[str]:
    """답변이 인용했지만 존재하지 않는 사실·판정 번호를 찾는 함수

    Args:
        draft: 답변 초안
        facts: 번호 붙은 사실 목록
        verdicts: 확정 판정 (항목 → {"id", ...})
    Returns:
        없는 번호 목록 (예: ["F99"])
    """
    known = {f["id"] for f in facts} | {v["id"] for v in verdicts.values()}
    return sorted({c for c in CITATION.findall(draft) if c not in known})


def check_answer(draft: str, facts: list[dict], verdicts: dict) -> list[str]:
    """답변 초안의 숫자·인용을 코드로 검사해 문제 목록을 반환하는 함수

    Args:
        draft: 답변 초안
        facts: 번호 붙은 사실 목록
        verdicts: 확정 판정
    Returns:
        문제 설명 목록 (비어 있으면 통과)
    """
    candidates = verdicts.get("fast_candidates", {}).get("value") or []
    issues = [f"근거 없는 숫자: {n}" for n in unbacked_numbers(draft, facts, (len(candidates),))]
    issues += [f"없는 인용 번호: {c}" for c in unknown_citations(draft, facts, verdicts)]
    return issues


def check_priority(result: dict, candidates: list[str], facts: list[dict]) -> list[str]:
    """AI의 충전기 후보 우선순위 판단이 쓸 수 있는지 코드로 확인하는 함수

    순서·이유 자체는 기준이 없어 고치지 않는다. 확정 후보만 썼는지, 이유가 그 거점의 사실을
    인용했는지, 이유에 근거 없는 숫자가 없는지만 본다.

    Args:
        result: {"order", "reasons": {station_id: {"text", "facts"}}}
        candidates: 권위 게이트가 확정한 충전기 후보
        facts: 번호 붙은 사실 목록
    Returns:
        문제 목록 (비어 있으면 채택)
    """
    issues = []
    if sorted(result.get("order") or []) != sorted(candidates):
        issues.append("순서가 확정 후보와 다름")
    by_id = {f["id"]: f for f in facts}
    for sid in candidates:
        reason = (result.get("reasons") or {}).get(sid) or {}
        text = reason.get("text") or ""
        if not text:
            issues.append(f"{sid} 이유 없음")
            continue
        if not any(by_id.get(x, {}).get("station_id") == sid for x in reason.get("facts", [])):
            issues.append(f"{sid} 이유에 그 거점의 사실 인용 없음")
        bad = unbacked_numbers(text, facts)
        if bad:
            issues.append(f"{sid} 이유에 근거 없는 숫자 {bad}")
        unknown = unknown_citations(text, facts, {})
        if unknown:
            issues.append(f"{sid} 이유에 없는 인용 {unknown}")
    return issues
