"""스냅샷의 서울 급속충전기 상태만 골라 작은 파일로 저장한다 (매일 감시용).

매시 수집 원본(ev_chargers_z11.csv, 약 18MB)은 git에서 빼고, 감시에 필요한 열만 커밋한다.

사용법:
    python3 -I build_fast_status.py <스냅샷 폴더> [<스냅샷 폴더> ...]

입력: ev_chargers_z11.csv
출력: fast_status.csv   (collected_at, statId, chgerId, busiNm, zscode, lat, lng, stat, statUpdDt, output)
"""

import sys
from pathlib import Path

import pandas as pd

COLUMNS = ["collected_at", "statId", "chgerId", "busiNm", "zscode", "lat", "lng", "stat",
           "statUpdDt", "output"]


def main(folder):
    folder = Path(folder)
    ev = pd.read_csv(folder / "ev_chargers_z11.csv", encoding="utf-8-sig", dtype=str)
    lat, lng = pd.to_numeric(ev["lat"], errors="coerce"), pd.to_numeric(ev["lng"], errors="coerce")
    # energy/store.py 의 급속 기준(서울 자치구 배정 6,045 기준)과 같은 조건
    keep = ((ev["delYn"] != "Y") & (ev["chgerType"] != "02")
            & ev["zscode"].str.startswith("11", na=False)
            & lat.between(36.8, 38.4) & lng.between(126.0, 127.9))
    out = ev.loc[keep, COLUMNS].drop_duplicates(subset=["statId", "chgerId"])
    out.to_csv(folder / "fast_status.csv", index=False, encoding="utf-8-sig")
    print(f"{folder.name}: 급속 {len(out)}개 → fast_status.csv")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        main(arg)
