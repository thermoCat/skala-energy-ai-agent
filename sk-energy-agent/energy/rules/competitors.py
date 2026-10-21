"""경쟁 현황·주간 경쟁 변화를 계산하는 순수 함수 (공개 충전기 데이터, 모든 회사에 같은 정보)"""
import numpy as np
import pandas as pd

from energy import criteria
from energy.store import NEAR_RADIUS_M, Snapshot, _to_meters


def is_own_operator(company: str, operator: str) -> bool:
    """충전 운영사가 회사 자신이나 계열사인지 알려주는 함수

    Args:
        company: 회사 (예: "GS칼텍스")
        operator: 충전기 운영사 이름 (busiNm)
    Returns:
        자사·계열이면 True
    """
    return operator in criteria.COMPANY_OPERATORS.get(company, (company,))


def charger_use(place: str, limit_yn: str) -> str:
    """충전기를 일반 이용·버스 전용·이용 제한으로 나누는 함수

    Args:
        place: 충전소 이름 (statNm)
        limit_yn: 이용자 제한 여부 (limitYn, "Y"/"N")
    Returns:
        이용 구분 이름
    """
    if limit_yn == "N":
        return criteria.USE_PUBLIC
    if limit_yn != "Y":
        return criteria.USE_UNKNOWN
    if any(w in str(place) for w in criteria.BUS_DEPOT_WORDS):
        return criteria.USE_BUS
    return criteria.USE_LIMITED


def watch_order_key(change: dict) -> tuple:
    """먼저 볼 변화의 정렬 기준 — 일반 이용 타사 신설을 먼저, 그다음 가까운 순"""
    public_rival = (change["change"] == "신설" and not change["own_operator"]
                    and change.get("use") == criteria.USE_PUBLIC)
    return (not public_rival, change["distance_m"])


def nearby_operators(snap: Snapshot, station_id: str) -> dict | None:
    """거점 반경 1km 급속충전기를 운영사별로 세는 함수 (경계 너머 경기·인천 포함)

    Args:
        snap: 스냅샷
        station_id: 주유소 번호
    Returns:
        전체 수와 운영사 상위 목록 dict, 없는 번호면 None
    """
    rows = snap.stations[snap.stations["UNI_ID"] == station_id]
    if rows.empty:
        return None
    st = rows.iloc[0]
    fast = pd.concat([snap.chargers.loc[snap.chargers["fast"], ["lat", "lng", "busiNm"]],
                      snap.border_fast[["lat", "lng", "busiNm"]]], ignore_index=True)
    y0, x0 = _to_meters(st["lat"], st["lng"])
    cy, cx = _to_meters(fast["lat"].to_numpy(), fast["lng"].to_numpy())
    near = fast[np.hypot(cy - y0, cx - x0) <= NEAR_RADIUS_M]
    counts = near["busiNm"].value_counts()
    top = [{"operator": op, "fast_ids": int(n)}
           for op, n in counts.head(criteria.NEARBY_TOP_OPERATORS).items()]
    return {"station_id": station_id, "name": st["OS_NM"], "gu": st["자치구"],
            "fast_1km": int(len(near)), "operators": int(counts.size), "top": top}


def _changes(history: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    first, last = history[0], history[-1]
    added = last[~last["key"].isin(first["key"])]
    removed = first[~first["key"].isin(last["key"])]
    return added, removed


def weekly_competition(history: list[pd.DataFrame], stations: pd.DataFrame,
                       company: str) -> dict:
    """비교 구간 처음과 끝 수집 사이에 자사 거점 주변 급속충전기가 어떻게 바뀌었는지 요약하는 함수

    Args:
        history: 오래된 순 급속 상태 표 목록
        stations: 주유소·LPG 표
        company: 회사
    Returns:
        구간, 서울 전체 신설·철거 수, 자사 거점 1km 안 신설·철거 목록(운영사·자사 여부·이용 구분·거리) dict
    """
    window = [history[0]["collected_at"].iloc[0], history[-1]["collected_at"].iloc[0]]
    added, removed = _changes(history) if len(history) >= 2 else (history[0][:0], history[0][:0])
    own = stations[stations["brand"] == company]
    sy, sx = _to_meters(own["lat"].to_numpy(), own["lng"].to_numpy())

    def near_own(df: pd.DataFrame, change: str) -> list[dict]:
        rows = []
        for _, r in df.iterrows():
            y, x = _to_meters(r["lat"], r["lng"])
            d = np.hypot(sy - y, sx - x)
            i = int(d.argmin())
            if d[i] <= NEAR_RADIUS_M:
                rows.append({"change": change, "charger": r["key"], "operator": r["busiNm"],
                             "own_operator": is_own_operator(company, r["busiNm"]),
                             "place": r.get("statNm"), "output_kw": r.get("output"),
                             "use": charger_use(r.get("statNm"), r.get("limitYn")),
                             "station_id": own.iloc[i]["UNI_ID"], "name": own.iloc[i]["OS_NM"],
                             "gu": own.iloc[i]["자치구"], "distance_m": int(round(d[i]))})
        return rows

    changes = near_own(added, "신설") + near_own(removed, "철거")
    competitor_new = [c for c in changes if c["change"] == "신설" and not c["own_operator"]]
    return {"company": company, "window": window,
            "seoul_added": int(len(added)), "seoul_removed": int(len(removed)),
            "changes": changes, "competitor_new": len(competitor_new),
            "competitor_new_public": sum(c["use"] == criteria.USE_PUBLIC for c in competitor_new),
            "report_needed": bool(changes)}
