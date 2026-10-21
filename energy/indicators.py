"""스냅샷에서 화면·답변에 쓰는 숫자를 계산하는 함수 모음 (판별은 하지 않음)"""
import math

import pandas as pd

from energy import criteria
from energy.store import Snapshot

FUELS = ("휘발유", "경유", "LPG", "전기", "수소")
FACILITY_NAMES = {"B027": "주유소", "K015": "LPG 충전소"}
STAT_NAMES = {"1": "통신이상", "2": "충전대기", "3": "충전중",
              "4": "운영중지", "5": "점검중", "9": "상태미확인"}
ABNORMAL_STATS = ("1", "4", "5", "9")
MAX_SESSION_MIN = 24 * 60


def change_rate(before, after):
    """두 시점 값의 증감률(%)을 계산하는 함수 (반올림 안 함)

    Args:
        before: 이전 시점 값 (숫자 또는 pandas Series)
        after: 이후 시점 값
    Returns:
        증감률 (%)
    """
    return (after - before) / before * 100


def fast_density(fast_ids, ev_count):
    """전기차 1만 대당 급속충전기 ID 수를 계산하는 함수

    Args:
        fast_ids: 급속충전기 ID 수
        ev_count: 전기차 등록 대수
    Returns:
        1만 대당 급속 ID 수
    """
    return fast_ids / ev_count * 10000


def ev_per_fast(fast_per_10k_ev: float) -> float:
    """급속 1개당 전기차 대수를 계산하는 함수 (화면용 — 판정은 1만 대당 값으로 한다)

    Args:
        fast_per_10k_ev: 전기차 1만 대당 급속 ID 수
    Returns:
        급속 ID 1개당 전기차 대수 (소수 첫째 자리)
    """
    return round(10000 / fast_per_10k_ev, 1)


def _count(snap: Snapshot, month: str, fuel: str, gu: str | None = None) -> int:
    m = snap.monthly
    rows = m[(m["기준월"] == month) & (m["연료"] == fuel)]
    if gu is not None:
        rows = rows[rows["자치구"] == gu]
    return int(rows["대수"].sum())


def station_card(snap: Snapshot, station_id: str) -> dict | None:
    """주유소 번호로 거점의 기본 정보를 반환하는 함수

    Args:
        snap: 스냅샷
        station_id: 오피넷 주유소 고유번호 (예: "A0009172")
    Returns:
        상호·회사·시설 종류·자치구·주유소 안 충전기 dict, 없는 번호면 None
    """
    rows = snap.stations[snap.stations["UNI_ID"] == station_id]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return {
        "station_id": station_id,
        "name": r["OS_NM"],
        "brand": r["brand"],
        "prodcd": r["prodcd"],
        "facility": FACILITY_NAMES.get(r["prodcd"], r["prodcd"]),
        "gu": r["자치구"],
        "lat": float(r["lat"]),
        "lng": float(r["lng"]),
        "gu_assignment": r["자치구_판정"],
        "in_station_charger": bool(r["주유소내_충전기"]),
        "in_station_fast_ids": int(r["주유소내_급속ID"]),
        "fast_1km": int(r["fast_1km"]),
        "fast_1km_busy_pct": _none_if_nan(r["fast_1km_busy_pct"]),
    }


def _none_if_nan(value):
    return None if value is None or math.isnan(value) else float(value)


def fuel_demand(snap: Snapshot, gu: str) -> dict:
    """자치구의 5개 연료 등록 대수와 1년 증감률을 반환하는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름 (예: "동대문구")
    Returns:
        연료별 이전·이후 대수, 증감, 증감률, 서울 전체 증감률 dict
    """
    base, cur = criteria.BASE_MONTH, criteria.CURRENT_MONTH
    fuels = {}
    for fuel in FUELS:
        before, after = _count(snap, base, fuel, gu), _count(snap, cur, fuel, gu)
        seoul_rate = change_rate(_count(snap, base, fuel), _count(snap, cur, fuel))
        all_rates = change_rate(fuel_count_by_gu(snap, fuel, base),
                                fuel_count_by_gu(snap, fuel, cur)).round(1)
        fuels[fuel] = {
            "before": before,
            "after": after,
            "change": after - before,
            "rate_pct": round(change_rate(before, after), 1),
            "seoul_rate_pct": round(seoul_rate, 1),
            # 1 = 25구 중 가장 많이 감소 / 가장 빠르게 증가 (동점은 같은 순위)
            "decline_rank": int(all_rates.rank(method="min")[gu]),
            "growth_rank": int(all_rates.rank(ascending=False, method="min")[gu]),
        }
    return {"gu": gu, "months": [base, cur], "gu_count": int(snap.monthly["자치구"].nunique()),
            "fuels": fuels}


def busy_pct(fast: pd.DataFrame) -> float | None:
    """급속충전기 중 충전 중 비율(%)을 계산하는 함수

    Args:
        fast: 급속충전기 행
    Returns:
        충전중 ÷ (충전대기 + 충전중) × 100, 상태가 유효한 충전기가 없으면 None
    """
    valid = fast[fast["stat"].isin(["2", "3"])]
    if valid.empty:
        return None
    return round(float((valid["stat"] == "3").mean() * 100), 1)


def busy_pct_by_gu(snap: Snapshot) -> pd.Series:
    """25개 자치구의 급속 충전 중 비율(%)을 반환하는 함수

    Args:
        snap: 스냅샷
    Returns:
        자치구 → 충전 중 비율 Series
    """
    fast = snap.chargers[snap.chargers["fast"]]
    return fast.groupby("gu").apply(busy_pct).astype(float)


def fast_ids_by_gu(snap: Snapshot) -> pd.Series:
    """25개 자치구의 급속충전기 ID 수를 반환하는 함수

    Args:
        snap: 스냅샷
    Returns:
        자치구 → 급속 ID 수 Series
    """
    return snap.chargers[snap.chargers["fast"]].groupby("gu").size()


def fuel_count_by_gu(snap: Snapshot, fuel: str, month: str) -> pd.Series:
    """25개 자치구의 한 연료 등록 대수를 반환하는 함수

    Args:
        snap: 스냅샷
        fuel: 연료 이름 (예: "전기")
        month: 기준월 (예: "2026.09")
    Returns:
        자치구 → 등록 대수 Series
    """
    m = snap.monthly
    rows = m[(m["기준월"] == month) & (m["연료"] == fuel)]
    return rows.groupby("자치구")["대수"].sum()


def fast_per_10k_ev_by_gu(snap: Snapshot) -> pd.Series:
    """25개 자치구의 전기차 1만 대당 급속 ID 수를 반환하는 함수 (반올림 안 함)

    Args:
        snap: 스냅샷
    Returns:
        자치구 → 1만 대당 급속 ID Series
    """
    return fast_density(fast_ids_by_gu(snap),
                        fuel_count_by_gu(snap, "전기", criteria.CURRENT_MONTH))


def ev_supply(snap: Snapshot, gu: str) -> dict:
    """자치구의 전기 충전 공급 지표를 반환하는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름
    Returns:
        급속·완속 ID 수, 급속 충전 중 비율, 전기차 1만 대당 급속 ID(자치구·서울) dict
    Raises:
        KeyError: 충전기가 배정되지 않은 자치구 이름일 때
    """
    ch = snap.chargers
    mine = ch[ch["gu"] == gu]
    if mine.empty:
        raise KeyError(gu)
    fast = mine[mine["fast"]]
    seoul_density = fast_density(int(ch["fast"].sum()),
                                 _count(snap, criteria.CURRENT_MONTH, "전기"))
    return {
        "gu": gu,
        "fast_ids": len(fast),
        "slow_ids": int((~mine["fast"]).sum()),
        "fast_busy_pct": busy_pct(fast),
        "fast_per_10k_ev": round(float(fast_per_10k_ev_by_gu(snap)[gu]), 1),
        # 1 = 25구 중 전기차 대비 급속이 가장 적음 (화면 값과 같은 소수 첫째 자리로 순위)
        "density_rank": int(fast_per_10k_ev_by_gu(snap).round(1).rank(method="min")[gu]),
        "seoul_fast_per_10k_ev": round(seoul_density, 1),
    }


def fast_status(snap: Snapshot, gu: str | None = None) -> dict:
    """급속충전기 상태 분포와 비정상(통신이상·운영중지·점검중·상태미확인) 비율을 반환하는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름. 없으면 서울 전체
    Returns:
        상태별 ID 수, 비정상 ID 수와 비율 dict
    """
    fast = snap.chargers[snap.chargers["fast"]]
    if gu is not None:
        fast = fast[fast["gu"] == gu]
    counts = fast["stat"].value_counts()
    abnormal = int(fast["stat"].isin(ABNORMAL_STATS).sum())
    return {
        "gu": gu,
        "fast_ids": len(fast),
        "by_status": {STAT_NAMES[k]: int(counts.get(k, 0)) for k in STAT_NAMES},
        "abnormal_ids": abnormal,
        "abnormal_pct": round(abnormal / len(fast) * 100, 1) if len(fast) else None,
    }


def session_minutes(snap: Snapshot, min_kw: float | None = None,
                    max_kw: float | None = None) -> pd.Series:
    """급속충전기별 마지막 1회 충전 시간(분)을 반환하는 함수

    Args:
        snap: 스냅샷
        min_kw: 이 출력 이상만 (없으면 제한 없음)
        max_kw: 이 출력 미만만 (없으면 제한 없음)
    Returns:
        0분 초과 24시간 미만인 충전 시간 Series
    """
    fast = snap.chargers[snap.chargers["fast"]]
    if min_kw is not None:
        fast = fast[fast["output_kw"] >= min_kw]
    if max_kw is not None:
        fast = fast[fast["output_kw"] < max_kw]
    # API가 충전기마다 마지막 시작·종료 시각만 주므로 마지막 1회만 쓸 수 있다
    start = pd.to_datetime(fast["lastTsdt"], format="%Y%m%d%H%M%S", errors="coerce")
    end = pd.to_datetime(fast["lastTedt"], format="%Y%m%d%H%M%S", errors="coerce")
    minutes = (end - start).dt.total_seconds() / 60
    return minutes[(minutes > 0) & (minutes < MAX_SESSION_MIN)]
