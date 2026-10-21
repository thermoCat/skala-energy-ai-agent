"""기준 규칙 테스트 (묶음·시나리오·이름 찾기·유휴 보류) — 2026-10-19 실데이터 스냅샷"""
import pytest

from energy import criteria
from energy.rules.cluster import review_cluster
from energy.rules.names import find_stations, normalize_name
from energy.indicators import busy_pct_by_gu
from energy.rules.score import (bottom_flags, district_baseline, ev_idle_flags,
                                h2_queue_flags, score_districts)
from energy.rules.simulate import simulate_fast_add
from energy.store import load_snapshot

SNAPSHOT = load_snapshot("2026-10-19")


# ── 자치구 기준 판정 ──────────────────────────────────────────────

def test_dongdaemun_lpg_not_idle_and_unjudgeable_items_on_hold():
    baseline = district_baseline(SNAPSHOT, "동대문구")
    assert baseline["lpg_idle"] is False          # 동대문 LPG -0.4%, 서울 -3.1%
    assert baseline["ev_idle"] is None            # 스냅샷 1개 → 판정 보류
    assert baseline["h2_queue"] is None           # 수집 1일 → 판정 보류
    assert baseline["score_rank"] == 1


def test_fuel_idle_is_bottom_20_percent_by_decline_rate():
    table = score_districts(SNAPSHOT)
    # 25구 중 5구 + 동점 (경유 -7.2% 강남·양천, LPG -4.7% 강남·성북)
    assert set(table.index[table["diesel_idle"]]) == {
        "용산구", "중구", "송파구", "동작구", "강남구", "양천구"}
    assert set(table.index[table["lpg_idle"]]) == {
        "광진구", "서대문구", "용산구", "양천구", "강남구", "성북구"}


def test_ev_idle_needs_three_snapshots():
    busy = busy_pct_by_gu(SNAPSHOT)
    assert ev_idle_flags([busy]) is None
    # 같은 실측값이 3번 연속이면 하위 20% 자치구가 그대로 전기 유휴
    flags = ev_idle_flags([busy, busy, busy])
    assert flags.equals(bottom_flags(busy, criteria.EV_IDLE_BOTTOM_RATIO))
    assert flags.sum() >= 5


def test_h2_queue_needs_three_days():
    assert h2_queue_flags(SNAPSHOT.h2) is None
    assert len(SNAPSHOT.h2) == 8                  # 서울 수소충전소 8곳


# ── 자사 묶음 기준 판정 ───────────────────────────────────────────

def test_dongdaemun_sk_cluster_fast_candidates():
    cluster = review_cluster(SNAPSHOT, "동대문구", "SK에너지")
    by_id = {s["station_id"]: s for s in cluster["stations"]}

    assert len(by_id) == 8 and cluster["cluster_median_fast_1km"] == 26.0
    candidates = {sid for sid, s in by_id.items() if s["verdict"] == "fast_candidate"}
    assert candidates == {"A0009172", "A0009248"}     # 풍한(10), 삼영(16)
    # 장안킹셀프(19)는 조건을 만족하지만 최대 2곳에 밀림
    assert by_id["A0009215"]["verdict"] == "keep" and by_id["A0009215"]["reasons"]
    # LPG 충전소는 급속 후보가 될 수 없음
    assert by_id["A0009251"]["verdict"] == "keep"


def test_every_cluster_card_carries_site_checks_and_conversion_types():
    cluster = review_cluster(SNAPSHOT, "동대문구", "SK에너지")
    assert len(cluster["site_checks"]) == 2
    assert len(cluster["conversion_types"]) == 5


def test_idle_risk_is_cluster_signal_not_station_badge():
    # 강남구: 충전기 부족 아님, 경유 감소율 하위 20% — 판매량이 없어 거점마다 붙이지 않는다
    cluster = review_cluster(SNAPSHOT, "강남구", "SK에너지")
    assert {s["verdict"] for s in cluster["stations"]} == {"keep"}
    diesel = next(f for f in cluster["fuel_signals"] if f["fuel"] == "경유")
    assert diesel["idle"] is True and diesel["rate_pct"] == -7.2
    assert diesel["own_facilities"] >= 2 and "판매량" in diesel["note"]


def test_dongdaemun_has_no_fuel_idle_signal():
    cluster = review_cluster(SNAPSHOT, "동대문구", "SK에너지")
    assert all(f["idle"] is False and f["note"] is None for f in cluster["fuel_signals"])


# ── 시나리오 ─────────────────────────────────────────────────────

def test_simulate_dongdaemun_plus_four_matches_hand_calculation():
    result = simulate_fast_add(SNAPSHOT, "A0009172", 4)
    # 손계산: (105 + 4) / 3,311대 × 10,000 = 329.2, 처리 대수 4 × 60 / 30.5분 = 7.9대
    assert result["before"]["fast_ids"] == 105 and result["after"]["fast_ids"] == 109
    assert result["after"]["fast_per_10k_ev"] == 329.2
    assert result["after"]["ev_bottleneck"] is True
    assert result["parking_spaces"] == 4
    assert result["occupancy_min"] == 30.5 and result["cars_per_hour"] == 7.9


def test_simulate_low_power_uses_low_power_median():
    result = simulate_fast_add(SNAPSHOT, "A0009172", 2, kw=50)
    assert result["occupancy_min"] == 46.0 and result["cars_per_hour"] == 2.6


def test_simulate_dongjak_plus_three_leaves_bottleneck():
    # 동작구: 점수 16(병목 경계) → 충전기 3대 신설하면 밀도 순위 4 → 5, 점수 17 로 병목에서 빠짐
    result = simulate_fast_add(SNAPSHOT, "A0000379", 3)
    assert result["before"]["ev_bottleneck"] is True
    assert result["after"]["ev_bottleneck"] is False
    assert (result["before"]["score"], result["after"]["score"]) == (16, 17)


def test_simulate_rejects_bad_input():
    with pytest.raises(ValueError):
        simulate_fast_add(SNAPSHOT, "A0009172", 0)
    with pytest.raises(KeyError):
        simulate_fast_add(SNAPSHOT, "A0000000", 2)


# ── 이름으로 찾기 ─────────────────────────────────────────────────

def test_find_station_by_name_unique():
    result = find_stations(SNAPSHOT.stations, "박미주유소")
    assert [c["station_id"] for c in result["candidates"]] == ["A0001146"]
    assert result["ambiguous"] is False


def test_same_name_without_company_asks_back():
    result = find_stations(SNAPSHOT.stations, "신성주유소")
    assert result["ambiguous"] is True
    assert {(c["brand"], c["gu"]) for c in result["candidates"]} == {
        ("알뜰(자영)", "구로구"), ("S-OIL", "노원구")}


def test_same_name_with_company_filter_is_unique():
    result = find_stations(SNAPSHOT.stations, "신성주유소", brand="S-OIL")
    assert [c["station_id"] for c in result["candidates"]] == ["A0010117"]


def test_name_normalization_handles_corp_and_self_marks():
    assert normalize_name("주)지유에너지직영 오렌지주유소") == "지유에너지직영오렌지주유소"
    result = find_stations(SNAPSHOT.stations, "삼원주유소")   # 실제 상호: (주)안국에너지 삼원셀프주유소
    assert [c["station_id"] for c in result["candidates"]] == ["A0009254"]
    assert find_stations(SNAPSHOT.stations, "없는주유소")["candidates"] == []
