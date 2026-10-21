"""대시보드 API 테스트 — FastAPI 앱을 수명 주기째 띄움 (실제 MCP 서버 + 실데이터 스냅샷 + fake LLM)"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """서버 lifespan(MCP 연결·그래프 생성)을 모듈에서 1번만 돌리는 fixture (임시 이력 DB 사용)"""
    before = os.environ.get("SESSION_DB_PATH")
    os.environ["SESSION_DB_PATH"] = str(tmp_path_factory.mktemp("api") / "api.db")
    with TestClient(server.app) as c:
        yield c
    if before is None:
        os.environ.pop("SESSION_DB_PATH")
    else:
        os.environ["SESSION_DB_PATH"] = before


def test_pins(client):
    data = client.get("/api/pins").json()
    assert len(data["pins"]) == 470
    assert sum(p["own"] for p in data["pins"]) == 130
    assert data["districts"]["동대문구"]["ev_bottleneck"] is True


def test_own_station_card_within_one_second(client):
    started = time.perf_counter()
    r = client.get("/api/stations/A0009172/card")
    assert time.perf_counter() - started < 1.0
    card = r.json()
    assert card["verdict"]["label"] == "⚡ 급속 후보" and len(card["site_checks"]) == 4


def test_other_company_card_and_unknown_station(client):
    assert "verdict" not in client.get("/api/stations/A0009177/card").json()
    assert client.get("/api/stations/A0000000/card").status_code == 404


def test_company_scope_changes_own_flag(client):
    # 같은 주유소라도 로그인 회사가 다르면 타사 카드가 된다
    card = client.get("/api/stations/A0009172/card", params={"company": "GS칼텍스"}).json()
    assert card["is_own"] is False and "verdict" not in card


def test_resolve(client):
    assert client.get("/api/stations/resolve", params={"q": "신성주유소"}).json()["ambiguous"] is True


def test_simulate(client):
    data = client.get("/api/stations/A0009172/simulate", params={"fast_add": 2}).json()
    assert data["cars_per_hour"] == 3.9
    assert client.get("/api/stations/A0009177/simulate",
                      params={"fast_add": 2}).status_code == 403     # 타사
    assert client.get("/api/stations/A0009172/simulate",
                      params={"fast_add": 0}).status_code == 422


def test_district_panel(client):
    data = client.get("/api/districts/동대문구").json()
    assert data["demand"]["fuels"]["전기"]["rate_pct"] == 57.0
    assert len(data["own_stations"]) == 8
    assert client.get("/api/districts/없는구").status_code == 404


def test_cluster(client):
    data = client.get("/api/clusters/동대문구").json()
    candidates = [s["station_id"] for s in data["stations"] if s["verdict"] == "fast_candidate"]
    assert sorted(candidates) == ["A0009172", "A0009248"]


def test_criteria(client):
    data = client.get("/api/criteria").json()
    assert data["rule_version"] and data["thresholds"]["fuel_idle_bottom_ratio"] == 0.2


def test_chat_with_gu_from_map_click(client):
    r = client.post("/chat", json={"message": "동대문구 SK 거점 8곳을 어떻게 나눠 써야 해?",
                                   "thread_id": "api-test", "gu": "동대문구"})
    meta = r.json()["metadata"]
    assert meta["request_type"] == "district" and meta["gate_corrections"] == 1
    assert meta["validation_passed"] is True


def test_record_decision_and_read_back(client):
    r = client.post("/api/decisions", json={"station_id": "A0009172", "choice": "보류",
                                            "conversion_type": "소형 추가 (초급속 1~2기)",
                                            "note": "부지 협의 필요"})
    assert r.status_code == 200 and r.json()["gu"] == "동대문구"
    log = client.get("/api/decisions", params={"station_id": "A0009172"}).json()
    assert log["human"][0]["note"] == "부지 협의 필요"


def test_decision_rules(client):
    other = client.post("/api/decisions", json={"station_id": "A0009177", "choice": "진행"})
    assert other.status_code == 403                                     # 타사 거점
    assert client.post("/api/decisions", json={"station_id": "A0009172",
                                               "choice": "사라"}).status_code == 422
    assert client.post("/api/decisions", json={"station_id": "A0009172", "choice": "진행",
                                               "conversion_type": "아무거나"}).status_code == 422


def test_watch_job_api_and_alert_inbox(client):
    quiet = client.post("/api/jobs/daily_watch/run", params={"gu": "광진구"}).json()
    assert quiet["alert_id"] is None and quiet["llm_calls"] == 0
    run = client.post("/api/jobs/daily_watch/run").json()
    assert run["alert_id"] is not None
    alerts = client.get("/api/alerts", params={"unread": True}).json()["alerts"]
    assert len(alerts) == 1 and len(alerts[0]["payload"]["stations"]) == 23
    assert client.post(f"/api/alerts/{run['alert_id']}/read").status_code == 200
    assert client.get("/api/alerts", params={"unread": True}).json()["alerts"] == []
    assert client.post(f"/api/alerts/{run['alert_id']}/read",
                       params={"company": "GS칼텍스"}).status_code == 404   # 다른 회사
    assert client.post("/api/jobs/monthly/run").status_code == 404      # 아직 없는 작업
    weekly = client.post("/api/jobs/weekly/run").json()
    assert weekly["alert_id"] is not None and "수서지점" in weekly["message"]
    comp = client.get("/api/stations/A0009177/competition").json()       # 타사 거점도 공개 정보
    assert comp["fast_1km"] > 0 and comp["top"]


def test_boundaries_cover_all_districts(client):
    data = client.get("/api/boundaries").json()
    names = {f["properties"]["name"] for f in data["features"]}
    assert len(names) == 25 and "동대문구" in names
