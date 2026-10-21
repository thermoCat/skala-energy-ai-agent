"""서울시 자치구별 연료별 자동차 등록현황(월간 xlsx)을 5개 연료 표로 정리한다.

사용법:
    python3 -I seoul_fuel.py <출력 CSV> <월별 xlsx> [<월별 xlsx> ...]

출력 CSV: 기준월, 자치구, 연료(휘발유/경유/LPG/전기/수소/기타), 비사업용 대수
원본: 서울 열린데이터광장 OA-15640 (월간, 국토교통부 자동차관리정보시스템)

연료 묶음:
- 하이브리드는 주유·충전하는 연료 쪽으로 넣는다 (휘발유+전기 → 휘발유, 경유+전기 → 경유, LPG+전기 → LPG).
- '수소'와 '수소전기'는 모두 수소충전소를 쓰므로 수소로 묶는다.
- CNG, LNG, 기타연료는 기타.
"""

import sys

import pandas as pd

FUEL_GROUP = {
    "휘발유": "휘발유", "휘발유(무연)": "휘발유", "휘발유(유연)": "휘발유", "하이브리드(휘발유+전기)": "휘발유",
    "경유": "경유", "하이브리드(경유+전기)": "경유",
    "엘피지": "LPG", "하이브리드(LPG+전기)": "LPG",
    "전기": "전기",
    "수소": "수소", "수소전기": "수소",
}


def load_month(path):
    raw = pd.read_excel(path, header=None, dtype=str)
    month = next(v.strip() for v in raw.iloc[5].dropna() if v.strip()[:2] == "20" and len(v.strip()) == 7)
    d = raw.iloc[8:].copy()
    d["자치구"] = d[0].where(d[0].notna() & (d[0].str.strip() != "")).ffill().str.strip()
    d["원연료"] = d[3].where(d[3].notna() & (d[3].str.strip() != "")).ffill().str.strip()
    d["용도"] = d[6].str.strip()
    d["대수"] = pd.to_numeric(d[15].str.replace(",", ""), errors="coerce")
    d = d.dropna(subset=["용도", "대수"])
    d = d[~d["자치구"].str.contains("합", na=False) & (d["용도"] == "비사업용")]
    d["연료"] = d["원연료"].map(FUEL_GROUP).fillna("기타")
    out = d.groupby(["자치구", "연료"], as_index=False)["대수"].sum()
    out.insert(0, "기준월", month)
    return out


def main(out_path, *paths):
    table = pd.concat([load_month(p) for p in paths], ignore_index=True)
    table["대수"] = table["대수"].astype(int)
    table.sort_values(["기준월", "자치구", "연료"]).to_csv(out_path, index=False, encoding="utf-8-sig")
    summary = table.pivot_table(index="연료", columns="기준월", values="대수", aggfunc="sum")
    print(summary.to_string())


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
