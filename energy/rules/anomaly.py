"""일간 추적 1차 감지 — 자사 거점 주변에 새로 멈춘 급속충전기와 멈춘 이유를 찾는 순수 함수 (LLM 없음)"""
import numpy as np
import pandas as pd

from energy import criteria
from energy.indicators import FACILITY_NAMES, STAT_NAMES
from energy.store import NEAR_RADIUS_M, _to_meters


def new_failures(history: list[pd.DataFrame]) -> pd.DataFrame:
    """직전 수집에서 정상이던 급속충전기 중 그 뒤 3회 연속 고장인 것을 고르는 함수

    Args:
        history: 오래된 순 급속 상태 표 목록 (load_status_history)
    Returns:
        마지막 수집 기준 새로 멈춘 충전기 표 (since = 고장으로 처음 본 수집 시각)
    """
    n = criteria.WATCH_CONSECUTIVE
    if len(history) < n + 1:
        return pd.DataFrame(columns=["key", "lat", "lng", "stat", "busiNm", "since"])
    before, *recent = [df.set_index("key") for df in history[-(n + 1):]]
    keys = sorted(set(before.index).intersection(*(set(df.index) for df in recent)))
    was_normal = before.loc[keys, "stat"].isin(criteria.NORMAL_STATS).to_numpy()
    always_bad = np.logical_and.reduce(
        [df.loc[keys, "stat"].isin(criteria.ALERT_STATS).to_numpy() for df in recent])
    last = recent[-1].loc[keys].copy()
    last["since"] = recent[0]["collected_at"].iloc[0]
    return last[was_normal & always_bad].reset_index()


def _reason_counts(chargers: pd.DataFrame) -> dict:
    # 멈춘 이유(통신이상·점검중·운영중지)를 많은 순으로 — "몇 개"보다 "왜"를 먼저 보여준다
    counts = chargers["stat"].map(STAT_NAMES).value_counts()
    return {name: int(n) for name, n in counts.items()}


def detect_anomalies(history: list[pd.DataFrame], stations: pd.DataFrame, company: str,
                     gu: str | None = None) -> dict:
    """회사 거점 주변 새 고장을 거점별로 묶어 하루 알림 1건 분량의 요약을 만드는 함수

    Args:
        history: 오래된 순 급속 상태 표 목록
        stations: 주유소·LPG 표
        company: 회사
        gu: 자치구 (주면 그 구 거점만 — 자치구 담당자용)
    Returns:
        감시 구간, 새로 멈춘 수, 멈춘 이유별 수, 많은 운영사, 영향 거점 목록(많은 순), 알림 필요 여부 dict
    """
    own = stations[stations["brand"] == company]
    if gu:
        own = own[own["자치구"] == gu]
    failures = new_failures(history)
    window = [df["collected_at"].iloc[0] for df in history[-(criteria.WATCH_CONSECUTIVE + 1):]]
    summary = {"company": company, "gu": gu, "window": window,
               "enough_history": len(history) >= criteria.WATCH_CONSECUTIVE + 1,
               "own_stations": len(own), "new_failures": 0, "reasons": {}, "operators": {},
               "stations": []}
    if own.empty or failures.empty:
        summary["alert_needed"] = False
        return summary

    sy, sx = _to_meters(own["lat"].to_numpy()[:, None], own["lng"].to_numpy()[:, None])
    cy, cx = _to_meters(failures["lat"].to_numpy()[None, :], failures["lng"].to_numpy()[None, :])
    near = np.hypot(sy - cy, sx - cx) <= NEAR_RADIUS_M        # 거점 × 새 고장 행렬

    rows = []
    for i, (_, st) in enumerate(own.iterrows()):
        hit = failures[near[i]]
        if hit.empty:
            continue
        rows.append({
            "station_id": st["UNI_ID"], "name": st["OS_NM"], "gu": st["자치구"],
            "facility": FACILITY_NAMES.get(st["prodcd"], st["prodcd"]),
            "new_failures": len(hit),
            "reasons": _reason_counts(hit),
            "chargers": [{"charger": r["key"], "operator": r["busiNm"],
                          "status": STAT_NAMES.get(r["stat"], r["stat"]),
                          "since": r["since"][:16]}
                         for _, r in hit.iterrows()],
        })
    rows.sort(key=lambda r: (-r["new_failures"], r["station_id"]))
    nearby = failures[near.any(axis=0)]
    # 한 운영사 충전기가 한꺼번에 멈추면 현장 고장보다 운영사 통신 장애일 수 있어 같이 보여준다
    operators = nearby["busiNm"].value_counts()
    summary.update(new_failures=len(nearby), reasons=_reason_counts(nearby),
                   operators={op: int(n) for op, n in operators.head(3).items()},
                   stations=rows, alert_needed=bool(rows))
    return summary
