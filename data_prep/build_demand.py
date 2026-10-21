"""시군구별 전기차 수요 테이블을 만든다.

사용법:
    python3 -I build_demand.py <교통안전공단 전기차 CSV> <환경공단 충전소 CSV> <국토부 등록현황 xlsx> <출력 폴더>

출력:
    region_demand.csv   시군구(시 단위) 키별 전기차 수요와 2022년 충전기 수
    coverage_report.md  세 데이터의 키가 얼마나 맞는지 점검 결과
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from region import (  # noqa: E402
    key_from_address,
    key_from_joined,
    normalize_group_label,
    region_key,
    sido_group,
    split_merged_sido,
)


def load_ev_registrations(path):
    df = pd.read_csv(path, encoding="cp949", dtype=str)
    df["계"] = pd.to_numeric(df["계"].str.replace(",", ""), errors="coerce").fillna(0).astype(int)
    keys = df["시군구별"].map(key_from_joined)
    df["시도"], df["시군구"] = keys.str[0], keys.str[1]
    pivot = df.pivot_table(index=["시도", "시군구"], columns="용도별", values="계", aggfunc="sum", fill_value=0)
    pivot = pivot.rename(columns={"비사업용": "ev_nonbiz_2025_04", "사업용": "ev_biz_2025_04"})
    pivot["ev_total_2025_04"] = pivot["ev_nonbiz_2025_04"] + pivot["ev_biz_2025_04"]
    return pivot.reset_index(), sorted(df["시군구별"].unique())


def charger_key(sido, gungu, address):
    """주소에서 키를 먼저 만들고, 주소가 이상하면 시도·군구 칸을 쓴다."""
    try:
        return key_from_address(address), "주소"
    except (ValueError, IndexError):
        return region_key(sido, gungu), "시도·군구 칸"


def load_chargers(path):
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, usecols=["시도", "군구", "주소", "기종(대)", "시설구분(소)"])
    results = [charger_key(s, g, a) for s, g, a in zip(df["시도"], df["군구"], df["주소"])]
    keys = [r[0] for r in results]
    column_keys = [region_key(s, g) for s, g in zip(df["시도"], df["군구"])]
    df["key_from"] = [r[1] for r in results]
    df["key_fixed"] = [k != c for k, c in zip(keys, column_keys)]
    df["key_시도"], df["key_시군구"] = [k[0] for k in keys], [k[1] for k in keys]
    load_chargers.fixed_rows = int(df["key_fixed"].sum())
    load_chargers.fallback_rows = int((df["key_from"] != "주소").sum())
    agg = df.groupby(["key_시도", "key_시군구"]).agg(
        chargers_2022=("기종(대)", "size"),
        fast_chargers_2022=("기종(대)", lambda s: int((s == "급속").sum())),
        station_chargers_2022=("시설구분(소)", lambda s: int((s == "주유소").sum())),
    )
    agg.index.names = ["시도", "시군구"]
    return agg.reset_index()


def load_molit(path):
    """국토부 2026.9: 시도(보정 그룹)별 비사업용 전기차 수, 시군구 키 목록."""
    fuel = pd.read_excel(path, "10.연료별_등록현황", header=None, dtype=str)
    header = fuel.iloc[2].tolist()
    fuel[0] = fuel[0].ffill()
    fuel[1] = fuel[1].ffill()
    row = fuel[(fuel[0].str.strip() == "전기") & (fuel[1].str.strip() == "소계") & (fuel[2].str.strip() == "비사업용")]
    groups = {}
    for col, label in enumerate(header):
        if col < 3 or not isinstance(label, str) or label.strip() in ("계", "합계"):
            continue
        groups[normalize_group_label(label)] = int(float(row.iloc[0, col]))

    sgg = pd.read_excel(path, "02.통계표_시군구", header=None, dtype=str)
    sgg[0] = sgg[0].ffill()
    rows = sgg.iloc[4:, [0, 1]].dropna()
    rows = rows[~rows[1].str.strip().isin(["계", "합계"]) & ~rows[0].str.strip().isin(["합계"])]
    keys = sorted({region_key(split_merged_sido(s, g), g) for s, g in zip(rows[0], rows[1])})
    return groups, keys


def main(ev_path, charger_path, molit_path, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ev, ev_raw_names = load_ev_registrations(ev_path)
    chargers = load_chargers(charger_path)
    molit_groups, molit_keys = load_molit(molit_path)

    # 2025.4 → 2026.9 보정 계수: 보정 그룹(광주+전남은 하나) 단위 비사업용 전기차 증가율
    ev["sido_group"] = ev["시도"].map(sido_group)
    base = ev.groupby("sido_group")["ev_nonbiz_2025_04"].sum()
    factor = {g: molit_groups[g] / base[g] for g in base.index}
    ev["scale_factor"] = ev["sido_group"].map(factor).round(4)
    ev["ev_nonbiz_est_2026_09"] = (ev["ev_nonbiz_2025_04"] * ev["scale_factor"]).round().astype(int)
    ev["ev_biz_share_pct"] = (ev["ev_biz_2025_04"] / ev["ev_total_2025_04"] * 100).round(1)

    table = ev.merge(chargers, on=["시도", "시군구"], how="outer", indicator=True)
    table = table.sort_values(["시도", "시군구"])
    cols = [
        "시도", "시군구", "ev_nonbiz_2025_04", "ev_biz_2025_04", "ev_total_2025_04", "ev_biz_share_pct",
        "sido_group", "scale_factor", "ev_nonbiz_est_2026_09",
        "chargers_2022", "fast_chargers_2022", "station_chargers_2022",
    ]
    table[cols].to_csv(out_dir / "region_demand.csv", index=False, encoding="utf-8-sig")

    ev_keys = set(zip(ev["시도"], ev["시군구"]))
    ch_keys = set(zip(chargers["시도"], chargers["시군구"]))
    mo_keys = set(molit_keys)

    def listing(keys):
        return [f"- {k}" for k in sorted(keys)] or ["- 없음"]

    lines = [
        "# 지명 정리 점검 결과",
        "",
        f"- 교통안전공단 원본 표기 {len(ev_raw_names)}개 → 정리 후 키 {len(ev_keys)}개",
        f"- 환경공단 충전소 키 {len(ch_keys)}개 (주소 기준으로 시도·시군구를 바로잡은 행 {load_chargers.fixed_rows}개, 주소를 못 읽어 칸을 쓴 행 {load_chargers.fallback_rows}개)",
        f"- 국토부 2026.9 시군구 키 {len(mo_keys)}개",
        f"- 전기차 ∩ 충전소 일치: {len(ev_keys & ch_keys)}개",
        f"- 전기차 ∩ 국토부 일치: {len(ev_keys & mo_keys)}개",
        "",
        "## 전기차에만 있는 키", *listing(ev_keys - ch_keys),
        "", "## 충전소에만 있는 키", *listing(ch_keys - ev_keys),
        "", "## 국토부에만 있는 키", *listing(mo_keys - ev_keys),
        "", "## 전기차에는 있는데 국토부에 없는 키", *listing(ev_keys - mo_keys),
        "", "## 보정 계수 (2026.9 비사업용 ÷ 2025.4 비사업용)",
        *[f"- {g}: {factor[g]:.3f} ({base[g]:,} → {molit_groups[g]:,})" for g in sorted(factor)],
        f"- 전국: {sum(molit_groups.values()) / base.sum():.3f} ({base.sum():,} → {sum(molit_groups.values()):,})",
    ]
    (out_dir / "coverage_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(*sys.argv[1:5])
