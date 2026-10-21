"""급속충전기를 더한다고 가정했을 때의 변화를 계산하는 순수 함수"""
from energy import criteria
from energy.indicators import session_minutes
from energy.rules.score import district_table, score_districts
from energy.store import Snapshot

def _district_view(table, gu: str) -> dict:
    row = table.loc[gu]
    return {
        "fast_ids": int(row["fast_ids"]),
        "fast_per_10k_ev": float(row["fast_per_10k_ev"]),
        "rank_density": int(row["rank_density"]),
        "score": int(row["score"]),
        "score_rank": int(row["score_rank"]),
        "ev_bottleneck": bool(row["ev_bottleneck"]),
    }


def simulate_fast_add(snap: Snapshot, station_id: str, fast_add: int,
                      kw: int = criteria.HIGH_POWER_KW) -> dict:
    """한 거점에 급속충전기 N대를 신설했을 때 자치구 순위·병목·주차면·처리 대수를 계산하는 함수

    Args:
        snap: 스냅샷
        station_id: 오피넷 주유소 고유번호
        fast_add: 더할 급속충전기 수 (1 이상)
        kw: 충전기 출력 kW
    Returns:
        전후 지표, 필요 주차면, 시간당 처리 대수, 가정값과 출처 dict
    Raises:
        KeyError: 없는 주유소 번호일 때
        ValueError: fast_add 가 1보다 작을 때
    """
    if fast_add < 1:
        raise ValueError("fast_add 는 1 이상이어야 합니다")
    rows = snap.stations[snap.stations["UNI_ID"] == station_id]
    if rows.empty:
        raise KeyError(station_id)
    gu = rows.iloc[0]["자치구"]

    high_power = kw >= criteria.HIGH_POWER_KW
    sessions = (session_minutes(snap, min_kw=criteria.HIGH_POWER_KW) if high_power
                else session_minutes(snap, max_kw=criteria.HIGH_POWER_KW))
    occupancy = round(float(sessions.median()), 1)
    power_text = (f"{criteria.HIGH_POWER_KW}kW 이상" if high_power
                  else f"{criteria.HIGH_POWER_KW}kW 미만")
    return {
        "station_id": station_id,
        "gu": gu,
        "fast_add": fast_add,
        "kw": kw,
        "before": _district_view(district_table(snap), gu),
        "after": _district_view(score_districts(snap, extra_fast={gu: fast_add}), gu),
        "parking_spaces": fast_add,
        "occupancy_min": occupancy,
        "cars_per_hour": round(fast_add * 60 / occupancy, 1),
        "assumptions": {
            "occupancy": f"서울 급속 {power_text} 마지막 1회 충전 시간 중앙값 "
                         f"(표본 {len(sessions)}개, 스냅샷 {snap.name})",
            "parking": "급속충전기 1대당 전용 주차면 1개",
        },
    }
