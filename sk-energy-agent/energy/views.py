"""MCP Tool·대시보드가 그대로 내보내는 응답을 조립하는 함수 모음 (LLM 없음)"""
import math

from energy import criteria
from energy.indicators import FACILITY_NAMES, session_minutes, station_card
from energy.rules.cluster import review_cluster
from energy.rules.score import district_table
from energy.store import NEAR_RADIUS_M, Snapshot


def _basic(row) -> dict:
    return {
        "station_id": row["UNI_ID"],
        "name": row["OS_NM"],
        "brand": row["brand"],
        "facility": FACILITY_NAMES.get(row["prodcd"], row["prodcd"]),
        "prodcd": row["prodcd"],
        "gu": row["자치구"],
    }


def station_card_view(snap: Snapshot, station_id: str, company: str,
                      with_verdict: bool = True) -> dict | None:
    """정보 패널용 주유소 카드를 만드는 함수

    자사 거점이면 묶음 기준 판정·연료 신호·전환 유형·현장 확인 항목을 붙이고,
    타사 거점이면 공개 정보만 담는다.

    Args:
        snap: 스냅샷
        station_id: 오피넷 주유소 고유번호
        company: 로그인 회사
        with_verdict: 기준 판정을 붙일지 (분석 에이전트용 조회는 False)
    Returns:
        카드 dict, 없는 번호면 None
    """
    card = station_card(snap, station_id)
    if card is None:
        return None
    card["is_own"] = card["brand"] == company
    if not (card["is_own"] and with_verdict):
        return card

    cluster = review_cluster(snap, card["gu"], company)
    mine = next(s for s in cluster["stations"] if s["station_id"] == station_id)
    card["verdict"] = {"code": mine["verdict"], "label": mine["label"],
                       "reasons": mine["reasons"]}
    # 카드에는 이 거점 시설의 연료 신호만 (주유소 → 경유, LPG 충전소 → LPG)
    card["fuel_signals"] = [f for f in cluster["fuel_signals"]
                            if f["idle"] and f["prodcd"] == card["prodcd"]]
    card["cluster_median_fast_1km"] = cluster["cluster_median_fast_1km"]
    card["conversion_types"] = cluster["conversion_types"]
    card["site_checks"] = cluster["site_checks"]
    card["rule_version"] = cluster["rule_version"]
    return card


def search_stations(snap: Snapshot, gu: str | None = None, brand: str | None = None,
                    prodcd: str | None = None) -> list[dict]:
    """자치구·회사·시설 종류로 거점 목록을 찾는 함수

    Args:
        snap: 스냅샷
        gu: 자치구 이름 (없으면 전체)
        brand: 회사 이름 (없으면 전체)
        prodcd: "B027" 주유소 / "K015" LPG (없으면 전체)
    Returns:
        거점 기본 정보 목록
    """
    st = snap.stations
    if gu:
        st = st[st["자치구"] == gu]
    if brand:
        st = st[st["brand"] == brand]
    if prodcd:
        st = st[st["prodcd"] == prodcd]
    return [_basic(r) for _, r in st.sort_values("UNI_ID").iterrows()]


def h2_status(snap: Snapshot, gu: str | None = None) -> dict:
    """서울 수소충전소별 대기 차량을 반환하는 함수 (좌표가 없어 자치구 단위)

    Args:
        snap: 스냅샷
        gu: 자치구 이름 (없으면 서울 전체)
    Returns:
        충전소 목록, 충전소 수, 대기 차량 합계 dict
    """
    h2 = snap.h2 if gu is None else snap.h2[snap.h2["gu"] == gu]
    stations = [{"station": r["station"], "gu": r["gu"], "wait": int(r["wait"]),
                 "collected_at": r["collected_at"].isoformat()}
                for _, r in h2.sort_values(["gu", "station"]).iterrows()]
    return {"gu": gu, "stations": stations, "station_count": len(stations),
            "wait_total": int(h2["wait"].sum())}


def map_pins(snap: Snapshot, company: str, brand: str | None = None) -> dict:
    """지도용 핀 470개와 자치구 색칠 값을 만드는 함수

    Args:
        snap: 스냅샷
        company: 로그인 회사 (자사 핀 강조·⚡ 배지 기준)
        brand: 이 회사 핀만 (없으면 전체)
    Returns:
        pins(좌표·회사·시설·자사 여부·배지)와 districts(병목·유휴 위험·수소) dict
    """
    badges = {}
    for gu in snap.stations.loc[snap.stations["brand"] == company, "자치구"].unique():
        for s in review_cluster(snap, gu, company)["stations"]:
            if s["verdict"] == "fast_candidate":
                badges[s["station_id"]] = "⚡"

    st = snap.stations if brand is None else snap.stations[snap.stations["brand"] == brand]
    pins = [{**_basic(r), "lat": float(r["lat"]), "lng": float(r["lng"]),
             "own": r["brand"] == company, "badge": badges.get(r["UNI_ID"])}
            for _, r in st.iterrows()]

    table = district_table(snap)
    h2 = snap.h2.groupby("gu")["wait"].agg(["size", "sum"])
    districts = {gu: {
        "ev_bottleneck": bool(row["ev_bottleneck"]),
        "diesel_idle": bool(row["diesel_idle"]),
        "lpg_idle": bool(row["lpg_idle"]),
        "h2_stations": int(h2["size"].get(gu, 0)),
        "h2_wait": int(h2["sum"].get(gu, 0)),
    } for gu, row in table.iterrows()}
    return {"company": company, "pins": pins, "districts": districts}


def nearby_districts(snap: Snapshot, gu: str, brand: str) -> list[dict]:
    """한 자치구에서 가까운 순으로 다른 자치구와 그 회사 거점 수를 반환하는 함수

    자치구 중심은 그 구 주유소·LPG 거점 좌표의 평균으로 잡는다.

    Args:
        snap: 스냅샷
        gu: 기준 자치구
        brand: 회사 이름
    Returns:
        {"gu", "distance_km", "own_count"} 목록 (가까운 순, 기준 자치구 제외)
    """
    st = snap.stations
    centers = st.groupby("자치구")[["lat", "lng"]].mean()
    own = st[st["brand"] == brand].groupby("자치구").size()
    lat0, lng0 = centers.loc[gu]
    km_lat = 111.0
    km_lng = 111.0 * math.cos(math.radians(lat0))
    rows = []
    for other, (lat, lng) in centers.drop(index=gu).iterrows():
        distance = math.hypot((lat - lat0) * km_lat, (lng - lng0) * km_lng)
        rows.append({"gu": other, "distance_km": round(distance, 1),
                     "own_count": int(own.get(other, 0))})
    return sorted(rows, key=lambda r: r["distance_km"])


def criteria_resource(snap: Snapshot) -> dict:
    """판별 기준 임계값·가정값을 한 묶음으로 반환하는 함수 (criteria://energy-mix)

    Args:
        snap: 스냅샷 (충전 시간 가정값 계산용)
    Returns:
        규칙 버전, 임계값, 가정값과 출처 dict
    """
    high = session_minutes(snap, min_kw=criteria.HIGH_POWER_KW)
    low = session_minutes(snap, max_kw=criteria.HIGH_POWER_KW)
    return {
        "rule_version": criteria.RULE_VERSION,
        "months": [criteria.BASE_MONTH, criteria.CURRENT_MONTH],
        "thresholds": {
            "ev_bottleneck_top_ratio": criteria.EV_BOTTLENECK_TOP_RATIO,
            "fuel_idle_bottom_ratio": criteria.FUEL_IDLE_BOTTOM_RATIO,
            "ev_idle_bottom_ratio": criteria.EV_IDLE_BOTTOM_RATIO,
            "ev_idle_min_snapshots": criteria.EV_IDLE_MIN_SNAPSHOTS,
            "h2_queue_min_days": criteria.H2_QUEUE_MIN_DAYS,
            "fast_candidate_max": criteria.FAST_CANDIDATE_MAX,
            "near_radius_m": NEAR_RADIUS_M,
        },
        "assumptions": {
            "occupancy_min_high_power": round(float(high.median()), 1),
            "occupancy_min_low_power": round(float(low.median()), 1),
            "high_power_kw": criteria.HIGH_POWER_KW,
            "source": f"서울 급속 마지막 1회 충전 시간 중앙값 (스냅샷 {snap.name}, "
                      f"표본 {len(high)}·{len(low)}개)",
        },
        "gated_items": criteria.GATED_ITEMS,
        "station_verdicts": criteria.STATION_VERDICTS,
        "site_checks": list(criteria.SITE_CHECK_ITEMS),
        "conversion_types": list(criteria.CONVERSION_TYPES),
    }
