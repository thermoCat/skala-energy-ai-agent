"""주유소 이름으로 거점을 찾는 함수 (같은 이름이 여러 곳이면 되묻기)"""
import re

import pandas as pd

from energy.indicators import FACILITY_NAMES

# 오피넷 상호의 법인 표기가 제각각이라("(주)", "㈜", "주)") 비교 전에 지운다
CORP_PATTERN = re.compile(r"\(?주\)|㈜|주식회사|\(유\)|\(합\)")
SELF_PATTERN = re.compile(r"셀프|self", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """상호에서 법인 표기·셀프 표기·공백·기호를 지워 비교용 문자열로 만드는 함수

    Args:
        name: 상호 (예: "SK에너지(주) 박미주유소")
    Returns:
        비교용 문자열 (예: "sk에너지박미주유소")
    """
    text = SELF_PATTERN.sub("", CORP_PATTERN.sub("", name))
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())


def find_stations(stations: pd.DataFrame, query: str, brand: str | None = None) -> dict:
    """이름으로 거점 후보를 찾고 되물어야 하는지 알려주는 함수

    Args:
        stations: 주유소·LPG 표
        query: 사용자가 말한 이름 (예: "신성주유소")
        brand: 회사 이름 필터 (없으면 전체)
    Returns:
        후보 목록과 ambiguous(후보 2곳 이상) dict
    """
    pool = stations if brand is None else stations[stations["brand"] == brand]
    q = normalize_name(query)
    names = pool["OS_NM"].map(normalize_name)
    exact = pool[names == q]
    hits = exact if not exact.empty else pool[names.str.contains(q, regex=False)] if q else exact
    candidates = [{
        "station_id": r["UNI_ID"],
        "name": r["OS_NM"],
        "gu": r["자치구"],
        "brand": r["brand"],
        "facility": FACILITY_NAMES.get(r["prodcd"], r["prodcd"]),
    } for _, r in hits.sort_values("UNI_ID").iterrows()]
    return {"query": query, "brand": brand, "candidates": candidates,
            "ambiguous": len(candidates) > 1}
