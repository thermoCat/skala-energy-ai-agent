"""일간 추적 테스트 — 실제 수집 4회(10-19 11:29, 10-20 19:54, 10-20 20:55, 10-21 09:02) 상태 데이터"""
from energy import criteria
from energy.rules.anomaly import detect_anomalies, new_failures
from energy.store import load_snapshot, load_status_history
from tests.conftest import event_types

HISTORY = load_status_history()
STATIONS = load_snapshot("2026-10-19").stations


def test_new_failures_need_normal_state_before():
    failures = new_failures(HISTORY)
    assert len(failures) == 39
    # 첫 수집부터 이미 고장이던 충전기는 운영사가 매일 다시 보고해도 "새로 멈춤"이 아니다
    first = HISTORY[0].set_index("key")["stat"].reindex(failures["key"])
    assert first.isin(criteria.NORMAL_STATS).all()


def test_sk_summary_tells_why_it_stopped():
    summary = detect_anomalies(HISTORY, STATIONS, "SK에너지")
    assert summary["alert_needed"] is True
    assert summary["new_failures"] == 16 and len(summary["stations"]) == 8
    assert summary["reasons"] == {"통신이상": 13, "점검중": 3}
    assert next(iter(summary["operators"])) == "피라인모터스"
    top = summary["stations"][0]
    assert (top["name"], top["gu"], top["reasons"]) == \
        ("서울동부고속주유소 ㈜아이에스피에너지", "노원구", {"통신이상": 8})


def test_quiet_district_and_short_history():
    assert detect_anomalies(HISTORY, STATIONS, "SK에너지", "광진구")["alert_needed"] is False
    short = detect_anomalies(HISTORY[-3:], STATIONS, "SK에너지")     # 정상이던 수집이 없음
    assert short["enough_history"] is False and short["alert_needed"] is False


def test_watch_job_makes_one_alert_with_llm(ask, store):
    result = ask("일간 추적", job="daily_watch")
    assert event_types(result) == ["RouteDecided", "JobChecked", "Judged", "Judged",
                                   "DraftWritten", "Validation", "JobSaved"]
    assert result["alert_id"] is not None and result["llm_calls"] == 3
    assert "서울동부고속주유소" in result["final"] and "통신이상 8개" in result["final"]
    assert result["validation"]["passed"] is True
    again = ask("일간 추적", job="daily_watch")                   # 같은 데이터로 다시 실행
    assert again["alert_id"] == result["alert_id"]
    assert len(store.list_alerts("SK에너지")) == 1


def test_quiet_watch_uses_no_llm(ask, store):
    result = ask("일간 추적", job="daily_watch", gu="광진구")
    assert event_types(result) == ["RouteDecided", "JobChecked", "JobSaved"]
    assert result.get("llm_calls", 0) == 0 and result.get("alert_id") is None
    row = store.conn.execute("SELECT llm_calls, alert_id FROM job_run").fetchone()
    assert row == (0, None)
    assert store.list_alerts("SK에너지") == []
