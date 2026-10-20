"""서울 거점 가까이 있는 경기·인천 급속충전기만 골라 작은 파일로 저장한다.

서울 경계 근처 주유소의 반경 1km 급속 수를 셀 때 경계 너머 충전기도 세기 위한 파일이다.
경기·인천 원본(ev_chargers_z28/z41.csv)은 용량 때문에 git에서 빠져 있어서, 필요한 행만 커밋한다.

사용법:
    python3 -I build_border_chargers.py <스냅샷 폴더>

입력 (스냅샷 폴더):
    ev_chargers_z11.csv, ev_chargers_z28.csv, ev_chargers_z41.csv
    seoul_stations.csv
출력 (스냅샷 폴더):
    ev_fast_border.csv   서울 거점 2km 안의 경기·인천 급속충전기
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_supply import _meters, load_chargers  # noqa: E402

# 지금 쓰는 반경은 1km지만 2km까지 늘려도 이 파일을 다시 만들 필요가 없게 여유를 둔다
MARGIN_M = 2000
COLUMNS = ["statId", "chgerId", "statNm", "addr", "lat", "lng", "busiNm", "stat",
           "lastTsdt", "lastTedt", "output", "zcode", "zscode", "kindDetail"]


def main(snap):
    snap = Path(snap)
    for z in ("28", "41"):
        if not (snap / f"ev_chargers_z{z}.csv").exists():
            sys.exit(f"ev_chargers_z{z}.csv 가 없습니다 — collect.py ev {z} 로 먼저 수집하세요")

    ev = load_chargers(snap)
    border = ev[ev["fast"] & ~ev["zscode"].str.startswith("11", na=False)]
    st = pd.read_csv(snap / "seoul_stations.csv", encoding="utf-8-sig")

    sy, sx = _meters(st["lat"].to_numpy()[:, None], st["lng"].to_numpy()[:, None])
    cy, cx = _meters(border["lat"].to_numpy()[None, :], border["lng"].to_numpy()[None, :])
    near_any = (np.hypot(sy - cy, sx - cx) <= MARGIN_M).any(axis=0)

    out = border.loc[near_any, COLUMNS]
    out.to_csv(snap / "ev_fast_border.csv", index=False, encoding="utf-8-sig")
    print(f"경기·인천 급속 {len(border)}개 중 서울 거점 {MARGIN_M}m 안 {len(out)}개 저장")


if __name__ == "__main__":
    main(sys.argv[1])
