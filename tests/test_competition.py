"""경쟁 현황·주유소 주간 추적 테스트 — 실제 수집 비교 (10-19 11:29 → 10-21 09:02)"""
from energy.rules.competitors import (charger_use, is_own_operator, nearby_operators,
                                     weekly_competition)
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
    assert (summary["seoul_added"], summary["seoul_removed"]) == (21, 2)
    [change] = [c for c in summary["changes"] if c["name"] == "(주)중앙에너비스 수서지점"]
    assert (change["operator"], change["distance_m"]) == ("SK일렉링크", 780)
    assert change["own_operator"] is False and summary["competitor_new"] == 13


def test_bus_depot_chargers_are_marked_and_public_ones_come_first():
    summary = weekly_competition(HISTORY, SNAPSHOT.stations, "SK에너지")
    bus = [c for c in summary["changes"] if c["place"] == "신흥운수 오금동차고지"]
    assert len(bus) == 8 and {c["use"] for c in bus} == {"버스 전용"}     # limitYn=Y 버스 차고지
    assert summary["competitor_new_public"] == 5                         # 타사 신설 13건 중 일반 이용
    assert charger_use("BMW차징스테이션", "N") == "일반 이용"
    assert charger_use("삼양교통", "Y") == "버스 전용"
    assert charger_use("아파트 지하주차장", "Y") == "이용 제한"
    assert charger_use("어딘가", None) == "이용 정보 없음"


def test_e1_has_no_change_nearby():
    assert weekly_competition(HISTORY, SNAPSHOT.stations, "E1")["report_needed"] is False


def test_weekly_job_report(ask, store):
    result = ask("주유소 주간 추적", job="weekly")
    assert event_types(result) == ["RouteDecided", "JobChecked", "Judged", "Judged",
                                   "DraftWritten", "Validation", "JobSaved"]
    assert result["llm_calls"] == 3 and result["alert_id"] is not None
    assert "태봉셀프주유소" in result["final"] and "GS차지비(타사) · 일반 이용" in result["final"]
    assert "그중 일반 이용 5건" in result["final"]
    assert result["validation"]["passed"] is True


def test_weekly_job_without_change_uses_no_llm(ask, store):
    result = ask("주유소 주간 추적", job="weekly", company="E1")
    assert event_types(result) == ["RouteDecided", "JobChecked", "JobSaved"]
    assert result.get("llm_calls", 0) == 0 and store.list_alerts("E1") == []


def test_station_answer_includes_nearby_competition(ask):
    own = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")
    assert "5) 주변 경쟁: 1km 안 급속 운영사 5곳" in own["final"]
    other = ask("이 주유소 주변 경쟁 현황은?", station_id="A0009177")
    assert "주변 경쟁" in other["final"] and "투자 판별은 하지 않습니다" in other["final"]
    assert other["validation"]["passed"] is True
