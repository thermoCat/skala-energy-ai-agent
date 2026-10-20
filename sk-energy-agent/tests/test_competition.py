"""경쟁 현황·주간 경쟁 추적 테스트 — 실제 수집 비교 (10-19 11:29 → 10-20 20:55)"""
from energy.rules.competitors import is_own_operator, nearby_operators, weekly_competition
from energy.store import load_snapshot, load_status_history
from tests.conftest import event_types

SNAPSHOT = load_snapshot("2026-10-19")
HISTORY = load_status_history()


def test_operator_affiliation():
    assert is_own_operator("GS칼텍스", "GS차지비") is True
    # SK일렉링크는 2025년 경영권이 앵커PE로 넘어가 SK 계열이 아님
    assert is_own_operator("SK에너지", "SK일렉링크") is False


def test_nearby_operators_for_punghan():
    comp = nearby_operators(SNAPSHOT, "A0009172")
    assert comp["fast_1km"] == 10                       # 카드의 반경 1km 급속 수와 같은 기준
    assert comp["top"][0] == {"operator": "한국전력", "fast_ids": 3}


def test_new_competitor_near_sk_suseo():
    summary = weekly_competition(HISTORY, SNAPSHOT.stations, "SK에너지")
    assert (summary["seoul_added"], summary["seoul_removed"]) == (1, 0)
    [change] = summary["changes"]
    assert (change["operator"], change["name"], change["distance_m"]) == \
        ("SK일렉링크", "(주)중앙에너비스 수서지점", 780)
    assert change["own_operator"] is False and summary["competitor_new"] == 1


def test_gs_has_no_change_nearby():
    assert weekly_competition(HISTORY, SNAPSHOT.stations, "GS칼텍스")["report_needed"] is False


def test_weekly_job_report(ask, store):
    result = ask("주간 경쟁", job="weekly")
    assert event_types(result) == ["RouteDecided", "JobChecked", "Judged", "Judged",
                                   "DraftWritten", "Validation", "JobSaved"]
    assert result["llm_calls"] == 3 and result["alert_id"] is not None
    assert "수서지점" in result["final"] and "SK일렉링크(타사)" in result["final"]
    assert result["validation"]["passed"] is True


def test_weekly_job_without_change_uses_no_llm(ask, store):
    result = ask("주간 경쟁", job="weekly", company="GS칼텍스")
    assert event_types(result) == ["RouteDecided", "JobChecked", "JobSaved"]
    assert result.get("llm_calls", 0) == 0 and store.list_alerts("GS칼텍스") == []


def test_station_answer_includes_nearby_competition(ask):
    own = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")
    assert "5) 주변 경쟁: 반경 1km 급속 운영사 5곳" in own["final"]
    other = ask("이 주유소 주변 경쟁 현황은?", station_id="A0009177")
    assert "주변 경쟁" in other["final"] and "투자 판별은 하지 않습니다" in other["final"]
    assert other["validation"]["passed"] is True
