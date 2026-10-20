"""자사 거점 묶음의 기준 판정을 계산하는 순수 함수 (권위 게이트 대조용)"""
import pandas as pd

from energy import criteria
from energy.indicators import FACILITY_NAMES
from energy.rules.score import district_baseline
from energy.store import Snapshot

FUEL_SIGNALS = (  # (연료, 기준 판정 키, 감소율 키, 시설 코드)
    ("경유", "diesel_idle", "diesel_rate_pct", "B027"),
    ("LPG", "lpg_idle", "lpg_rate_pct", "K015"),
)


def own_stations(snap: Snapshot, gu: str, brand: str) -> pd.DataFrame:
    """자치구 안 한 회사의 주유소·LPG 거점을 반환하는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름
        brand: 회사 이름 (예: "SK에너지")
    Returns:
        거점 표
    """
    st = snap.stations
    return st[(st["자치구"] == gu) & (st["brand"] == brand)]


def fast_candidates(own: pd.DataFrame, ev_bottleneck: bool) -> tuple[set, set]:
    """급속 후보 조건을 만족하는 거점과 그중 실제 후보(최대 2곳)를 고르는 함수

    Args:
        own: 묶음 거점 표
        ev_bottleneck: 자치구 전기 병목 여부
    Returns:
        (조건 충족 거점 번호 집합, 후보로 고른 거점 번호 집합)
    """
    if not ev_bottleneck or own.empty:
        return set(), set()
    median = own["fast_1km"].median()
    eligible = own[(own["prodcd"] == "B027") & ~own["주유소내_충전기"].astype(bool)
                   & (own["fast_1km"] <= median)]
    # 같은 수요를 나눠 먹지 않게 반경 급속이 적은 곳부터 최대 2곳만
    chosen = eligible.sort_values(["fast_1km", "UNI_ID"]).head(criteria.FAST_CANDIDATE_MAX)
    return set(eligible["UNI_ID"]), set(chosen["UNI_ID"])


def _station_verdict(row: pd.Series, eligible: set, chosen: set,
                     median: float) -> tuple[str, list[str]]:
    if row["UNI_ID"] in chosen:
        return "fast_candidate", [
            "전기 병목 자치구",
            "주유소 안 충전기 없음",
            f"반경 1km 급속 {row['fast_1km']}개 ≤ 묶음 중앙값 {median:g}개",
        ]
    if row["UNI_ID"] in eligible:
        return "keep", [f"급속 후보 조건은 충족하지만 반경 급속이 더 적은 "
                        f"{criteria.FAST_CANDIDATE_MAX}곳에 밀림"]
    return "keep", []


def fuel_signals(district: dict, own: pd.DataFrame) -> list[dict]:
    """경유·LPG 유휴 위험을 묶음 단위 신호로 만드는 함수

    판매량이 없어 같은 자치구 거점끼리 구별할 근거가 없으므로 거점별 판정으로 내리지 않는다.

    Args:
        district: 자치구 기준 판정
        own: 묶음 거점 표
    Returns:
        연료별 유휴 위험 여부·감소율·해당 시설 자사 거점 수·안내 문구 목록
    """
    signals = []
    for fuel, item, rate_key, prodcd in FUEL_SIGNALS:
        count = int((own["prodcd"] == prodcd).sum())
        idle = district[item]
        signals.append({
            "fuel": fuel,
            "item": item,
            "idle": idle,
            "label": criteria.FUEL_IDLE_LABEL if idle else None,
            "rate_pct": district[rate_key],
            "own_facilities": count,
            "note": (f"{fuel} 1년 감소율이 서울 25구 중 큰 쪽 하위 20%. 판매량이 없어 거점별로 "
                     f"구분하지 않음 — 자사 {FACILITY_NAMES[prodcd]} {count}곳의 역할 조정은 "
                     f"판매량 확인 후 검토") if idle and count else None,
        })
    return signals


def review_cluster(snap: Snapshot, gu: str, brand: str) -> dict:
    """자치구 안 자사 거점 묶음의 거점별 기준 판정을 반환하는 함수 (게이트 전용)

    Args:
        snap: 스냅샷
        gu: 자치구 이름
        brand: 회사 이름
    Returns:
        자치구 기준 판정, 묶음 중앙값, 거점별 판정·이유, 연료 신호, 전환 유형·현장 확인 항목 dict
    """
    district = district_baseline(snap, gu)
    own = own_stations(snap, gu, brand)
    eligible, chosen = fast_candidates(own, district["ev_bottleneck"])
    median = float(own["fast_1km"].median()) if not own.empty else None

    stations = []
    for _, row in own.sort_values("UNI_ID").iterrows():
        verdict, reasons = _station_verdict(row, eligible, chosen, median)
        stations.append({
            "station_id": row["UNI_ID"],
            "name": row["OS_NM"],
            "facility": FACILITY_NAMES.get(row["prodcd"], row["prodcd"]),
            "fast_1km": int(row["fast_1km"]),
            "in_station_charger": bool(row["주유소내_충전기"]),
            "verdict": verdict,
            "label": criteria.STATION_VERDICTS[verdict],
            "reasons": reasons,
        })
    return {
        "gu": gu,
        "brand": brand,
        "district": district,
        "cluster_median_fast_1km": median,
        "stations": stations,
        "fuel_signals": fuel_signals(district, own),
        "conversion_types": list(criteria.CONVERSION_TYPES),
        "site_checks": list(criteria.SITE_CHECK_ITEMS),
        "rule_version": criteria.RULE_VERSION,
    }
