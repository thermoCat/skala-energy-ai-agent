"""스냅샷 CSV를 한 번 읽어 메모리에 들고 있는 데이터 계층"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
MONTHLY_CSV = DATA_DIR / "derived" / "seoul_fuel_monthly.csv"


@dataclass(frozen=True)
class Snapshot:
    """한 시점 스냅샷의 표 묶음"""
    name: str                  # 스냅샷 폴더 이름 (예: "2026-10-19")
    stations: pd.DataFrame     # 주유소·LPG 470곳
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


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig",
                       dtype={"UNI_ID": str, "기준월": str})


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
    return Snapshot(
        name=name,
        stations=_read_csv(folder / "seoul_stations.csv"),
        balance=_read_csv(folder / "seoul_fuel_balance.csv").set_index("자치구"),
        monthly=_read_csv(MONTHLY_CSV),
    )
