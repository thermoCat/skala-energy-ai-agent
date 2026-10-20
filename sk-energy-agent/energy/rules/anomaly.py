"""매일 감시 1차 감지 — 자사 거점 주변에 새로 멈춘 급속충전기를 찾는 순수 함수 (LLM 없음)"""
import numpy as np
import pandas as pd

from energy import criteria
from energy.indicators import FACILITY_NAMES, STAT_NAMES
from energy.store import NEAR_RADIUS_M, _to_meters


def new_failures(history: list[pd.DataFrame]) -> pd.DataFrame:
    """최근 수집 내내 고장이고, 고장으로 바뀐 지 24시간 이내인 급속충전기를 고르는 함수

    Args:
        history: 오래된 순 급속 상태 표 목록 (load_status_history)
    Returns:
        마지막 수집 기준 새 고장 충전기 표 (since = 고장으로 바뀐 시각)
    """
    n = criteria.WATCH_CONSECUTIVE
    if len(history) < n:
        return pd.DataFrame(columns=["key", "lat", "lng", "stat", "since"])
    recent = [df.set_index("key") for df in history[-n:]]
    keys = sorted(set.intersection(*(set(df.index) for df in recent)))
    always_bad = np.logical_and.reduce(
        [df.loc[keys, "stat"].isin(criteria.ALERT_STATS).to_numpy() for df in recent])
    last = recent[-1].loc[keys].copy()
    collected = pd.Timestamp(last["collected_at"].iloc[0])
    last["since"] = pd.to_datetime(last["statUpdDt"], format="%Y%m%d%H%M%S", errors="coerce")
    fresh = (collected - last["since"]) <= pd.Timedelta(hours=criteria.NEW_FAILURE_HOURS)
    return last[always_bad & fresh.to_numpy()].reset_index()


def detect_anomalies(history: list[pd.DataFrame], stations: pd.DataFrame, company: str,
                     gu: str | None = None) -> dict:
    """회사 거점 주변 새 고장을 거점별로 묶어 하루 알림 1건 분량의 요약을 만드는 함수

    Args:
        history: 오래된 순 급속 상태 표 목록
        stations: 주유소·LPG 표
        company: 회사
        gu: 자치구 (주면 그 구 거점만 — 자치구 담당자용)
    Returns:
        감시 구간, 새 고장 수, 영향 거점 목록(새 고장 많은 순), 알림 필요 여부 dict
    """
    own = stations[stations["brand"] == company]
    if gu:
        own = own[own["자치구"] == gu]
    failures = new_failures(history)
    window = [df["collected_at"].iloc[0] for df in history[-criteria.WATCH_CONSECUTIVE:]]
    summary = {"company": company, "gu": gu, "window": window,
               "enough_history": len(history) >= criteria.WATCH_CONSECUTIVE,
               "own_stations": len(own), "new_failures": 0, "stations": []}
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
            "chargers": [{"charger": r["key"], "operator": r["busiNm"],
                          "status": STAT_NAMES.get(r["stat"], r["stat"]),
                          "since": r["since"].isoformat(timespec="minutes")}
                         for _, r in hit.iterrows()],
        })
    rows.sort(key=lambda r: (-r["new_failures"], r["station_id"]))
    summary.update(new_failures=int(near.any(axis=0).sum()), stations=rows,
                   alert_needed=bool(rows))
    return summary
