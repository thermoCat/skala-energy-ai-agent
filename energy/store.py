"""스냅샷 CSV를 한 번 읽어 메모리에 들고 있는 데이터 계층

자치구 단위 급속충전기 수는 모든 계산에서 "서울 자치구가 배정된 급속 ID" 한 가지 기준만 쓴다.
거점 반경 급속 수만 경계 너머 경기·인천 급속을 더한다 — 운전자는 시 경계를 가리지 않으므로.
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
MONTHLY_CSV = DATA_DIR / "derived" / "seoul_fuel_monthly.csv"

NEAR_RADIUS_M = 1000          # 거점 주변 급속 수를 세는 반경
METERS_PER_DEGREE = 111_000.0
CHARGER_COLUMNS = ["statId", "chgerId", "chgerType", "addr", "lat", "lng", "busiNm",
                   "stat", "lastTsdt", "lastTedt", "output", "zscode", "kindDetail", "delYn"]


@dataclass(frozen=True)
class Snapshot:
    """한 시점 스냅샷의 표 묶음"""
    name: str                  # 스냅샷 폴더 이름 (예: "2026-10-19")
    stations: pd.DataFrame     # 주유소·LPG 470곳 (+ fast_1km)
    chargers: pd.DataFrame     # 서울 자치구가 배정된 충전기 (급속·완속)
    border_fast: pd.DataFrame  # 서울 거점 2km 안 경기·인천 급속 (반경 계산에만 사용)
    h2: pd.DataFrame           # 서울 수소충전소 대기 차량 (수집 시각별)
    balance: pd.DataFrame      # 자치구 수급표 (index = 자치구)
    monthly: pd.DataFrame      # 기준월 × 자치구 × 연료 등록 대수


def latest_snapshot_name() -> str:
    """가장 최근 날짜 스냅샷 폴더 이름을 반환하는 함수

    Returns:
        폴더 이름 (예: "2026-10-19")
    """
    # 매시 수집 폴더("2026-10-19T13")에는 충전기·수소만 있어 날짜 폴더만 고른다
    names = sorted(p.name for p in SNAPSHOT_DIR.iterdir()
                   if p.is_dir() and "T" not in p.name)
    return names[-1]


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def load_chargers(folder: Path) -> pd.DataFrame:
    """서울 충전기 원본에서 자치구가 배정된 충전기만 골라 반환하는 함수

    Args:
        folder: 스냅샷 폴더
    Returns:
        statId·chgerId·gu·lat·lng·fast·stat·output_kw 등이 담긴 표
    """
    ev = _read_csv(folder / "ev_chargers_z11.csv", dtype=str, usecols=CHARGER_COLUMNS)
    ev = ev[ev["delYn"] != "Y"].copy()   # 삭제 표시된 충전기가 섞이면 급속 수가 부풀려진다
    ev["lat"] = pd.to_numeric(ev["lat"], errors="coerce")
    ev["lng"] = pd.to_numeric(ev["lng"], errors="coerce")
    ev = ev.dropna(subset=["lat", "lng"])
    # 좌표가 수도권 밖으로 잘못 입력된 행 제외
    ev = ev[ev["lat"].between(36.8, 38.4) & ev["lng"].between(126.0, 127.9)]
    ev = ev[ev["zscode"].str.startswith("11", na=False)].copy()
    ev["gu"] = ev["zscode"].map(_gu_names(ev))
    ev["zscode"] = ev["zscode"].astype(str)
    ev["fast"] = ev["chgerType"] != "02"   # 02 = AC 완속
    ev["output_kw"] = pd.to_numeric(ev["output"], errors="coerce")
    return ev.drop(columns=["delYn", "chgerType", "output"]).reset_index(drop=True)


def load_border_fast(folder: Path) -> pd.DataFrame:
    """서울 거점 가까이 있는 경기·인천 급속충전기를 반환하는 함수

    Args:
        folder: 스냅샷 폴더
    Returns:
        lat·lng·stat 표. 파일이 없는 예전 스냅샷이면 빈 표
    """
    path = folder / "ev_fast_border.csv"
    if not path.exists():
        return pd.DataFrame(columns=["lat", "lng", "stat"])
    border = _read_csv(path, dtype={"stat": str, "zscode": str})
    border["fast"] = True
    return border


def load_h2(folder: Path, gu_names: dict) -> pd.DataFrame:
    """서울 수소충전소 대기 차량 기록을 반환하는 함수

    Args:
        folder: 스냅샷 폴더
        gu_names: 시군구 코드 → 자치구 이름
    Returns:
        collected_at·station·code·gu·wait 표 (수집 시각 × 충전소마다 1행)
    """
    h2 = _read_csv(folder / "h2_wait.csv", dtype=str)
    h2 = h2[h2["chrstn_mno"].str[:2] == "11"]
    # 같은 수집 시각에 같은 충전소가 여러 번 들어 있어 1행만 남긴다
    h2 = h2.drop_duplicates(subset=["collected_at", "chrstn_mno"])
    return pd.DataFrame({
        "collected_at": pd.to_datetime(h2["collected_at"]),
        "station": h2["chrstn_nm"],
        "code": h2["chrstn_mno"],
        "gu": h2["chrstn_mno"].str[:5].map(gu_names),
        "wait": pd.to_numeric(h2["wait_vhcle_alge"], errors="coerce").fillna(0).astype(int),
    }).reset_index(drop=True)


def _gu_names(ev: pd.DataFrame) -> dict:
    # 시군구 코드 → 자치구 이름. 주소 두 번째 단어의 최빈값 (data_prep/build_supply.py 와 같은 규칙)
    token = ev["addr"].str.split().str[1]
    return token.groupby(ev["zscode"]).agg(lambda s: s.value_counts().index[0]).to_dict()


def _to_meters(lat, lng, lat0: float = 37.55):
    return lat * METERS_PER_DEGREE, lng * METERS_PER_DEGREE * np.cos(np.radians(lat0))


def add_fast_nearby(stations: pd.DataFrame, fast: pd.DataFrame,
                    radius_m: int = NEAR_RADIUS_M) -> pd.DataFrame:
    """거점마다 반경 안 급속 ID 수와 급속 충전 중 비율을 붙여 반환하는 함수

    Args:
        stations: 주유소·LPG 표 (lat, lng)
        fast: 급속충전기 표 (lat, lng, stat)
        radius_m: 반경 (m)
    Returns:
        fast_1km, fast_1km_busy_pct 컬럼이 추가된 표
    """
    sy, sx = _to_meters(stations["lat"].to_numpy()[:, None], stations["lng"].to_numpy()[:, None])
    cy, cx = _to_meters(fast["lat"].to_numpy()[None, :], fast["lng"].to_numpy()[None, :])
    near = np.hypot(sy - cy, sx - cx) <= radius_m          # 거점 × 급속 ID 행렬

    # 충전 중 비율은 충전대기(2)·충전중(3) 상태만 분모로 — 자치구 수급표와 같은 정의
    valid = fast["stat"].isin(["2", "3"]).to_numpy()
    busy = (fast["stat"] == "3").to_numpy()
    valid_n = (near & valid).sum(axis=1)
    busy_n = (near & busy).sum(axis=1)

    out = stations.copy()
    out["fast_1km"] = near.sum(axis=1)
    out["fast_1km_busy_pct"] = np.where(
        valid_n > 0, np.round(busy_n / np.maximum(valid_n, 1) * 100, 1), np.nan)
    return out


def load_status_history(limit: int = 4, until: str | None = None) -> list[pd.DataFrame]:
    """최근 스냅샷들의 서울 급속 상태 표를 오래된 순으로 반환하는 함수 (일간·주간 추적용)

    Args:
        limit: 최근 몇 개를 읽을지 (일간 추적 = 정상이던 1회 + 연속 고장 3회)
        until: 이 폴더 이름까지만 읽음 (없으면 SNAPSHOT_UNTIL 환경변수, 그것도 없으면 전부)
    Returns:
        fast_status.csv 표 목록 (key = "statId-chgerId" 컬럼 포함)
    """
    # 수집이 쌓이면 "최근 3개"가 바뀌므로, 테스트는 SNAPSHOT_UNTIL 로 기준 시점을 고정한다
    until = until or os.environ.get("SNAPSHOT_UNTIL")
    files = [p / "fast_status.csv" for p in SNAPSHOT_DIR.iterdir()
             if (p / "fast_status.csv").exists() and (until is None or p.name <= until)]
    frames = []
    for path in files:
        df = _read_csv(path, dtype=str)
        df["key"] = df["statId"] + "-" + df["chgerId"]
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lng"] = pd.to_numeric(df["lng"], errors="coerce")
        frames.append(df)
    frames.sort(key=lambda df: df["collected_at"].iloc[0])
    return frames[-limit:]


@lru_cache(maxsize=4)
def load_snapshot(name: str | None = None) -> Snapshot:
    """스냅샷 폴더를 읽어 Snapshot 으로 반환하는 함수

    Args:
        name: 스냅샷 폴더 이름. 없으면 가장 최근 날짜
    Returns:
        Snapshot (같은 이름은 두 번째부터 메모리에서 바로 반환)
    """
    name = name or latest_snapshot_name()
    folder = SNAPSHOT_DIR / name
    chargers = load_chargers(folder)
    border_fast = load_border_fast(folder)
    stations = _read_csv(folder / "seoul_stations.csv", dtype={"UNI_ID": str})
    # '판정'은 자치구 배정 신뢰도라 투자 판정과 헷갈리지 않게 이름을 바꿔 쓴다 (예전 스냅샷 호환)
    stations = stations.rename(columns={"판정": "자치구_판정"})
    nearby_fast = pd.concat([chargers.loc[chargers["fast"], ["lat", "lng", "stat"]],
                             border_fast[["lat", "lng", "stat"]]], ignore_index=True)
    return Snapshot(
        name=name,
        stations=add_fast_nearby(stations, nearby_fast),
        chargers=chargers,
        border_fast=border_fast,
        h2=load_h2(folder, dict(zip(chargers["zscode"], chargers["gu"]))),
        balance=_read_csv(folder / "seoul_fuel_balance.csv").set_index("자치구"),
        monthly=_read_csv(MONTHLY_CSV, dtype={"기준월": str}),
    )
