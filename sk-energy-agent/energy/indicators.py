"""스냅샷에서 화면·답변에 쓰는 숫자를 계산하는 함수 모음 (판별은 하지 않음)"""
from energy import criteria
from energy.store import Snapshot

FUELS = ("휘발유", "경유", "LPG", "전기", "수소")
FACILITY_NAMES = {"B027": "주유소", "K015": "LPG 충전소"}


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
        "in_station_charger": bool(r["주유소내_충전기"]),
        "in_station_fast_ids": int(r["주유소내_급속ID"]),
    }


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
        fuels[fuel] = {
            "before": before,
            "after": after,
            "change": after - before,
            "rate_pct": round(change_rate(before, after), 1),
            "seoul_rate_pct": round(seoul_rate, 1),
        }
    return {"gu": gu, "months": [base, cur], "fuels": fuels}


def ev_supply(snap: Snapshot, gu: str) -> dict:
    """자치구의 전기 충전 공급 지표를 반환하는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름
    Returns:
        급속·완속 ID 수, 급속 충전 중 비율, 전기차 1만 대당 급속 ID(자치구·서울) dict
    """
    b = snap.balance
    row = b.loc[gu]
    return {
        "gu": gu,
        "fast_ids": int(row["급속충전기ID"]),
        "slow_ids": int(row["완속충전기ID"]),
        "fast_busy_pct": float(row["급속_충전중비율%"]),
        "fast_per_10k_ev": round(fast_density(row["급속충전기ID"], row["전기"]), 1),
        "seoul_fast_per_10k_ev": round(
            fast_density(b["급속충전기ID"].sum(), b["전기"].sum()), 1),
    }
