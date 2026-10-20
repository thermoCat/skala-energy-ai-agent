"""자치구 기준 판정을 계산하는 순수 함수 (권위 게이트 대조용)"""
import pandas as pd

from energy import criteria
from energy.indicators import change_rate, fast_density
from energy.store import Snapshot


def score_districts(snap: Snapshot) -> pd.DataFrame:
    """25개 자치구의 전기 병목·경유 유휴 위험 기준 판정 표를 만드는 함수

    Args:
        snap: 스냅샷
    Returns:
        자치구별 지표·순위·점수·기준 판정 표 (index = 자치구)
    """
    base, cur = criteria.BASE_MONTH, criteria.CURRENT_MONTH
    counts = snap.monthly.pivot_table(index="자치구", columns=["연료", "기준월"],
                                      values="대수", aggfunc="sum")
    # 순위는 화면에 보이는 소수 첫째 자리 값으로 매김 — 같은 숫자로 보이는 두 구(강동·동작 46.6%)가
    # 다른 순위를 받으면 사람이 손으로 검산할 수 없다
    table = pd.DataFrame({
        "ev_growth_pct": change_rate(counts[("전기", base)], counts[("전기", cur)]).round(1),
        "fast_per_10k_ev": fast_density(snap.balance["급속충전기ID"],
                                        snap.balance["전기"]).round(1),
    })
    table["rank_growth"] = table["ev_growth_pct"].rank(ascending=False, method="min")
    table["rank_density"] = table["fast_per_10k_ev"].rank(method="min")
    table["score"] = table["rank_growth"] + table["rank_density"]
    top_n = max(1, int(len(table) * criteria.EV_BOTTLENECK_TOP_RATIO))
    cutoff = table["score"].nsmallest(top_n).iloc[-1]
    table["ev_bottleneck"] = table["score"] <= cutoff

    # 오피넷 재수집 전이라 거점 수 변화 조건은 빼고 감소율 조건만 본다
    diesel = change_rate(counts[("경유", base)], counts[("경유", cur)])
    seoul_diesel = change_rate(counts[("경유", base)].sum(), counts[("경유", cur)].sum())
    table["diesel_rate_pct"] = diesel.round(1)
    table["diesel_idle"] = diesel < seoul_diesel
    return table


def district_baseline(snap: Snapshot, gu: str) -> dict:
    """한 자치구의 기준 판정을 반환하는 함수 (게이트 전용 — 분석 에이전트에게 보여주지 않음)

    Args:
        snap: 스냅샷
        gu: 자치구 이름
    Returns:
        ev_bottleneck·diesel_idle 기준 값과 점수, rule_version dict
    """
    row = score_districts(snap).loc[gu]
    return {
        "gu": gu,
        "ev_bottleneck": bool(row["ev_bottleneck"]),
        "diesel_idle": bool(row["diesel_idle"]),
        "score": int(row["score"]),
        "rule_version": criteria.RULE_VERSION,
    }
