"""자치구 기준 판정 테스트 — 2026-10-19 실데이터 스냅샷"""
from energy.rules.score import district_baseline, score_districts
from energy.store import load_snapshot

SNAPSHOT = load_snapshot("2026-10-19")


def test_ev_bottleneck_is_reference_six_districts():
    table = score_districts(SNAPSHOT)
    bottleneck = set(table.index[table["ev_bottleneck"]])
    assert bottleneck == {"동대문구", "성북구", "강동구", "송파구", "광진구", "동작구"}


def test_dongdaemun_scores_match_reference_rank_file():
    table = score_districts(SNAPSHOT)
    assert table.loc["동대문구", "score"] == 3      # seoul_ev_bottleneck_rank.csv 합산 값
    assert table.loc["동대문구", "ev_growth_pct"] == 57.0


def test_dongdaemun_diesel_not_idle_because_milder_than_seoul():
    # 동대문 경유 -4.8%, 서울 -6.6% → 서울보다 덜 줄어 유휴 위험 아님
    baseline = district_baseline(SNAPSHOT, "동대문구")
    assert baseline["ev_bottleneck"] is True
    assert baseline["diesel_idle"] is False
