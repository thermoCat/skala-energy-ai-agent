"""매일 감시 테스트 — 실제 수집 3회(10-19 11:29, 10-20 19:54, 10-20 20:55) 상태 데이터"""
from energy.rules.anomaly import detect_anomalies, new_failures
from energy.store import load_snapshot, load_status_history
from tests.conftest import event_types

HISTORY = load_status_history()
STATIONS = load_snapshot("2026-10-19").stations


def test_new_failures_are_recent_ones_only():
    # 3회 연속 고장 186개 중, 고장으로 바뀐 지 24시간 이내인 것만
    assert len(new_failures(HISTORY)) == 68


def test_sk_summary_is_one_alert_worth():
    summary = detect_anomalies(HISTORY, STATIONS, "SK에너지")
    assert summary["alert_needed"] is True
    assert summary["new_failures"] == 39 and len(summary["stations"]) == 23
    top = summary["stations"][0]
    assert (top["name"], top["gu"], top["new_failures"]) == ("광호주유소", "서대문구", 6)


def test_quiet_district_and_short_history():
    assert detect_anomalies(HISTORY, STATIONS, "SK에너지", "광진구")["alert_needed"] is False
    short = detect_anomalies(HISTORY[-1:], STATIONS, "SK에너지")
    assert short["enough_history"] is False and short["alert_needed"] is False


def test_watch_job_makes_one_alert_with_llm(ask, store):
    result = ask("매일 감시", job="daily_watch")
    assert event_types(result) == ["RouteDecided", "JobChecked", "Judged", "Judged",
                                   "DraftWritten", "Validation", "JobSaved"]
    assert result["alert_id"] is not None and result["llm_calls"] == 3
    assert "광호주유소" in result["final"] and result["validation"]["passed"] is True
    again = ask("매일 감시", job="daily_watch")                   # 같은 데이터로 다시 실행
    assert again["alert_id"] == result["alert_id"]
    assert len(store.list_alerts("SK에너지")) == 1


def test_quiet_watch_uses_no_llm(ask, store):
    result = ask("매일 감시", job="daily_watch", gu="광진구")
    assert event_types(result) == ["RouteDecided", "JobChecked", "JobSaved"]
    assert result.get("llm_calls", 0) == 0 and result.get("alert_id") is None
    row = store.conn.execute("SELECT llm_calls, alert_id FROM job_run").fetchone()
    assert row == (0, None)
    assert store.list_alerts("SK에너지") == []
