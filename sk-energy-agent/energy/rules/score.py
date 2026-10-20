"""자치구 기준 판정을 계산하는 순수 함수 (권위 게이트 대조용)"""
from functools import lru_cache, reduce

import pandas as pd

from energy import criteria
from energy.indicators import (busy_pct_by_gu, change_rate, fast_density, fast_ids_by_gu,
                               fuel_count_by_gu)
from energy.store import Snapshot, load_snapshot


def bottom_flags(values: pd.Series, ratio: float) -> pd.Series:
    """값이 낮은 순 상위 비율(동점 포함)에 드는지 표시하는 함수

    Args:
        values: 자치구 → 값
        ratio: 비율 (예: 0.2 → 25구 중 5구)
    Returns:
        자치구 → bool Series
    """
    top_n = max(1, int(len(values) * ratio))
    return values <= values.nsmallest(top_n).iloc[-1]


def ev_idle_flags(busy_history: list[pd.Series]) -> pd.Series | None:
    """급속 충전 중 비율이 최근 스냅샷마다 연속으로 서울 하위 20%인 자치구를 표시하는 함수

    Args:
        busy_history: 스냅샷별 자치구 → 급속 충전 중 비율(%) 목록 (오래된 순)
    Returns:
        자치구 → bool Series. 스냅샷이 모자라면 None (판정 보류)
    """
    n = criteria.EV_IDLE_MIN_SNAPSHOTS
    if len(busy_history) < n:
        return None
    flags = [bottom_flags(s, criteria.EV_IDLE_BOTTOM_RATIO) for s in busy_history[-n:]]
    return reduce(lambda a, b: a & b, flags)


def h2_queue_flags(h2: pd.DataFrame) -> pd.Series | None:
    """같은 충전소·같은 시간대에 대기 차량이 있는 날이 3일 이상인 자치구를 표시하는 함수

    Args:
        h2: 수소 대기 기록 (collected_at, code, gu, wait)
    Returns:
        자치구 → bool Series. 수집한 날이 모자라면 None (판정 보류)
    """
    n = criteria.H2_QUEUE_MIN_DAYS
    if h2["collected_at"].dt.date.nunique() < n:
        return None
    waiting = h2[h2["wait"] > 0].assign(day=lambda d: d["collected_at"].dt.date,
                                        hour=lambda d: d["collected_at"].dt.hour)
    days = waiting.groupby(["gu", "code", "hour"])["day"].nunique()
    return (days >= n).groupby(level="gu").any()


def score_districts(snap: Snapshot, extra_fast: dict[str, int] | None = None) -> pd.DataFrame:
    """25개 자치구의 전기 병목·유휴 위험·수소 병목 기준 판정 표를 만드는 함수

    Args:
        snap: 스냅샷
        extra_fast: 자치구 → 더한다고 가정할 급속 ID 수 (시나리오용, 없으면 실제 값)
    Returns:
        자치구별 지표·순위·점수·기준 판정 표 (index = 자치구)
    """
    base, cur = criteria.BASE_MONTH, criteria.CURRENT_MONTH
    fast_ids = fast_ids_by_gu(snap).add(pd.Series(extra_fast or {}, dtype=float),
                                        fill_value=0)
    # 순위는 화면에 보이는 소수 첫째 자리 값으로 매김 — 같은 숫자로 보이는 두 구(강동·동작 46.6%)가
    # 다른 순위를 받으면 사람이 손으로 검산할 수 없다
    table = pd.DataFrame({
        "ev_growth_pct": change_rate(fuel_count_by_gu(snap, "전기", base),
                                     fuel_count_by_gu(snap, "전기", cur)).round(1),
        "fast_ids": fast_ids.astype(int),
        "fast_per_10k_ev": fast_density(fast_ids, fuel_count_by_gu(snap, "전기", cur)).round(1),
    })
    table["rank_growth"] = table["ev_growth_pct"].rank(ascending=False, method="min")
    table["rank_density"] = table["fast_per_10k_ev"].rank(method="min")
    table["score"] = table["rank_growth"] + table["rank_density"]
    table["score_rank"] = table["score"].rank(method="min")
    table["ev_bottleneck"] = bottom_flags(table["score"], criteria.EV_BOTTLENECK_TOP_RATIO)

    # 오피넷 재수집 전이라 거점 수 변화 조건은 빼고 감소율 조건만 본다
    for fuel, key in (("경유", "diesel"), ("LPG", "lpg")):
        rate = change_rate(fuel_count_by_gu(snap, fuel, base),
                           fuel_count_by_gu(snap, fuel, cur)).round(1)
        table[f"{key}_rate_pct"] = rate
        table[f"{key}_idle"] = bottom_flags(rate, criteria.FUEL_IDLE_BOTTOM_RATIO)

    # 지금은 스냅샷이 1개라 두 판정 모두 보류(None)
    ev_idle = ev_idle_flags([busy_pct_by_gu(snap)])
    h2_queue = h2_queue_flags(snap.h2)
    table["ev_idle"] = None if ev_idle is None else ev_idle.reindex(table.index)
    table["h2_queue"] = (None if h2_queue is None
                         else h2_queue.reindex(table.index, fill_value=False))
    return table


@lru_cache(maxsize=8)
def _cached_table(snapshot_name: str) -> pd.DataFrame:
    return score_districts(load_snapshot(snapshot_name))


def district_table(snap: Snapshot) -> pd.DataFrame:
    """스냅샷의 자치구 기준 판정 표를 반환하는 함수 (스냅샷마다 1번만 계산)

    Args:
        snap: 스냅샷
    Returns:
        score_districts 결과 표
    """
    return _cached_table(snap.name)


def _bool_or_none(value) -> bool | None:
    return None if value is None or pd.isna(value) else bool(value)


def district_baseline(snap: Snapshot, gu: str) -> dict:
    """한 자치구의 기준 판정을 반환하는 함수 (게이트 전용 — 분석 에이전트에게 보여주지 않음)

    Args:
        snap: 스냅샷
        gu: 자치구 이름
    Returns:
        병목·유휴 위험 기준 값과 순위·점수, rule_version dict
    """
    row = district_table(snap).loc[gu]
    return {
        "gu": gu,
        "ev_bottleneck": bool(row["ev_bottleneck"]),
        "diesel_idle": bool(row["diesel_idle"]),
        "lpg_idle": bool(row["lpg_idle"]),
        "diesel_rate_pct": float(row["diesel_rate_pct"]),
        "lpg_rate_pct": float(row["lpg_rate_pct"]),
        "ev_idle": _bool_or_none(row["ev_idle"]),
        "h2_queue": _bool_or_none(row["h2_queue"]),
        "score": int(row["score"]),
        "score_rank": int(row["score_rank"]),
        "rule_version": criteria.RULE_VERSION,
    }
