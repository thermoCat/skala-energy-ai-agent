"""서울 자치구 × 5개 연료 수급 테이블을 만든다 (스냅샷 폴더 하나 기준).

사용법:
    python3 -I build_supply.py <스냅샷 폴더> <seoul_fuel_monthly.csv> <기준월 예: 2026.09>

입력 (스냅샷 폴더):
    opinet/opinet_around_B027_*.json   주유소 (휘발유 판매 = 주유소 전체)
    opinet/opinet_around_K015_*.json   LPG 충전소
    ev_chargers_z11.csv (+ z28, z41)    전기 충전기 (경기·인천은 경계 판정용)
    h2_wait.csv                         수소충전소 대기 차량
출력 (스냅샷 폴더):
    seoul_stations.csv      서울 주유소·LPG 충전소 목록 (상표, 좌표, 자치구, 주유소 내 충전기 여부)
    seoul_fuel_balance.csv  자치구 × 연료별 수요(차량)와 공급(거점·충전기), 병목 신호
"""

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from katec import katec_to_wgs84  # noqa: E402

BRAND = {"SKE": "SK에너지", "GSC": "GS칼텍스", "HDO": "HD현대오일뱅크", "SOL": "S-OIL",
         "RTO": "알뜰(자영)", "RTX": "알뜰(고속도로)", "NHO": "알뜰(농협)", "ETC": "자가상표",
         "E1G": "E1", "SKG": "SK가스"}
FAR_M = 2000  # 가장 가까운 충전기가 이보다 멀면 자치구 판정을 '불확실'로 둔다


def _meters(lat, lng, lat0=37.55):
    return lat * 111_000.0, lng * 111_000.0 * np.cos(np.radians(lat0))


def load_opinet(snap, prodcd):
    rows = {}
    for f in sorted(glob.glob(f"{snap}/opinet/opinet_around_{prodcd}_*.json")):
        for o in json.load(open(f, encoding="utf-8"))["OIL"]:
            rows[o["UNI_ID"]] = o
    df = pd.DataFrame(rows.values())
    if df.empty:
        return df
    ll = [katec_to_wgs84(float(x), float(y)) for x, y in zip(df["GIS_X_COOR"], df["GIS_Y_COOR"])]
    df["lat"], df["lng"] = [p[0] for p in ll], [p[1] for p in ll]
    df["brand"] = df["POLL_DIV_CD"].map(BRAND).fillna(df["POLL_DIV_CD"])
    df["prodcd"] = prodcd
    return df


def load_chargers(snap):
    frames = []
    for z in ("11", "28", "41"):
        p = Path(snap) / f"ev_chargers_z{z}.csv"
        if p.exists():
            frames.append(pd.read_csv(p, encoding="utf-8-sig", dtype=str))
    ev = pd.concat(frames, ignore_index=True)
    ev = ev[ev["delYn"] != "Y"].copy()
    ev["lat"], ev["lng"] = pd.to_numeric(ev["lat"], errors="coerce"), pd.to_numeric(ev["lng"], errors="coerce")
    ev = ev.dropna(subset=["lat", "lng"])
    ev = ev[ev["lat"].between(36.8, 38.4) & ev["lng"].between(126.0, 127.9)]  # 수도권 밖 오입력 제외
    ev["fast"] = ev["chgerType"] != "02"  # 02 = AC 완속
    return ev


def gu_names(ev):
    """zscode → 자치구 이름 (서울 충전기 주소의 두 번째 단어 중 최빈값)."""
    seoul = ev[ev["zscode"].str.startswith("11", na=False)]
    tok = seoul["addr"].str.split().str[1]
    return tok.groupby(seoul["zscode"]).agg(lambda s: s.value_counts().index[0]).to_dict()


def assign_zscode(df, ev):
    ey, ex = _meters(ev["lat"].to_numpy(), ev["lng"].to_numpy())
    codes = ev["zscode"].to_numpy()
    out_code, out_dist = [], []
    for lat, lng in zip(df["lat"], df["lng"]):
        y, x = _meters(lat, lng)
        d = np.hypot(ey - y, ex - x)
        i = int(np.argmin(d))
        out_code.append(codes[i]); out_dist.append(float(d[i]))
    df["zscode"], df["nearest_charger_m"] = out_code, np.round(out_dist)
    return df


def main(snap, demand_csv, month):
    snap = str(snap)
    ev = load_chargers(snap)
    names = gu_names(ev)

    st = pd.concat([load_opinet(snap, "B027"), load_opinet(snap, "K015")], ignore_index=True)
    st = assign_zscode(st, ev)
    st = st[st["zscode"].str.startswith("11", na=False)].copy()
    st["자치구"] = st["zscode"].map(names)
    st["자치구_판정"] = np.where(st["nearest_charger_m"] > FAR_M, "불확실", "확정")

    # 주유소 안 충전기: 시설구분 '주유소'(E007) 충전기가 반경 150m 안에 있으면 있다고 본다
    ev_st = ev[ev["kindDetail"] == "E007"]
    sy, sx = _meters(ev_st["lat"].to_numpy(), ev_st["lng"].to_numpy())
    has_ev, fast_n = [], []
    for lat, lng in zip(st["lat"], st["lng"]):
        y, x = _meters(lat, lng)
        m = np.hypot(sy - y, sx - x) <= 150
        has_ev.append(bool(m.any())); fast_n.append(int(ev_st["fast"].to_numpy()[m].sum()))
    st["주유소내_충전기"] = has_ev
    st["주유소내_급속ID"] = fast_n
    cols = ["UNI_ID", "prodcd", "brand", "POLL_DIV_CD", "OS_NM", "PRICE", "lat", "lng", "자치구", "zscode", "nearest_charger_m", "자치구_판정", "주유소내_충전기", "주유소내_급속ID"]
    st[cols].to_csv(Path(snap) / "seoul_stations.csv", index=False, encoding="utf-8-sig")

    # 공급 집계
    gas = st[st["prodcd"] == "B027"]
    lpg = st[st["prodcd"] == "K015"]
    seoul_ev = ev[ev["zscode"].str.startswith("11", na=False)].copy()
    seoul_ev["자치구"] = seoul_ev["zscode"].map(names)
    fast = seoul_ev[seoul_ev["fast"]]
    fast_valid = fast[fast["stat"].isin(["2", "3"])]
    h2 = pd.read_csv(Path(snap) / "h2_wait.csv", encoding="utf-8-sig", dtype=str)
    h2 = h2[h2["chrstn_mno"].str[:2] == "11"].copy()
    h2["자치구"] = h2["chrstn_mno"].str[:5].map(names)
    h2["wait"] = pd.to_numeric(h2["wait_vhcle_alge"], errors="coerce").fillna(0)

    sup = pd.DataFrame({
        "주유소": gas.groupby("자치구").size(),
        "SK주유소": gas[gas["POLL_DIV_CD"] == "SKE"].groupby("자치구").size(),
        "LPG충전소": lpg.groupby("자치구").size(),
        "급속충전기ID": fast.groupby("자치구").size(),
        "완속충전기ID": seoul_ev[~seoul_ev["fast"]].groupby("자치구").size(),
        "급속_충전중비율%": (fast_valid["stat"] == "3").groupby(fast_valid["자치구"]).mean() * 100,
        "수소충전소": h2.groupby("자치구").size(),
        "수소_대기차량": h2.groupby("자치구")["wait"].sum(),
    }).fillna(0)

    dem = pd.read_csv(demand_csv, encoding="utf-8-sig", dtype={"기준월": str})
    dem = dem[dem["기준월"] == month].pivot_table(index="자치구", columns="연료", values="대수", aggfunc="sum")
    bal = dem[["휘발유", "경유", "LPG", "전기", "수소"]].join(sup, how="outer").fillna(0)
    bal["휘발유·경유 1만대당 주유소"] = (bal["주유소"] / ((bal["휘발유"] + bal["경유"]) / 10_000)).round(2)
    bal["LPG 1만대당 충전소"] = (bal["LPG충전소"] / (bal["LPG"] / 10_000)).round(2)
    bal["전기차 1만대당 급속ID"] = (bal["급속충전기ID"] / (bal["전기"] / 10_000)).round(1)
    bal["수소차 1천대당 충전소"] = (bal["수소충전소"] / (bal["수소"] / 1_000)).replace([np.inf], np.nan).round(2)
    bal["급속_충전중비율%"] = bal["급속_충전중비율%"].round(1)
    bal = bal.round(0).astype({c: int for c in ["휘발유", "경유", "LPG", "전기", "수소", "주유소", "SK주유소", "LPG충전소", "급속충전기ID", "완속충전기ID", "수소충전소", "수소_대기차량"]})
    bal.to_csv(Path(snap) / "seoul_fuel_balance.csv", encoding="utf-8-sig")

    print(f"서울 주유소 {len(gas)}곳 (SK {int((gas['POLL_DIV_CD'] == 'SKE').sum())}곳), LPG 충전소 {len(lpg)}곳, 자치구 판정 불확실 {int((st['자치구_판정'] == '불확실').sum())}곳")
    print(f"서울 급속충전기 ID {len(fast)}개, 완속 {int((~seoul_ev['fast']).sum())}개, 수소충전소 {len(h2)}곳 (대기 {int(h2['wait'].sum())}대)")
    print(f"SK 주유소 중 주유소 안 충전기 있음: {int(gas[(gas['POLL_DIV_CD'] == 'SKE')]['주유소내_충전기'].sum())}곳")
    return bal


if __name__ == "__main__":
    b = main(*sys.argv[1:4])
    show = ["주유소", "SK주유소", "LPG충전소", "전기", "급속충전기ID", "전기차 1만대당 급속ID", "급속_충전중비율%", "수소", "수소충전소", "수소_대기차량"]
    print(b[show].sort_values("전기차 1만대당 급속ID").to_string())
