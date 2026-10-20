"""에너지 MCP Tool 테스트 — 실제 MCP 서버 하위 프로세스 + 2026-10-19 실데이터 스냅샷"""
import asyncio
import json
import time

import pytest

from agent.mcp_client import MCPClient

ENERGY_TOOLS = {"get_station_card", "get_fuel_demand", "get_ev_supply", "get_h2_status",
                "search_stations", "resolve_station", "get_map_pins", "score_district",
                "review_cluster", "simulate"}


async def _serve(client: MCPClient, ready: asyncio.Event, done: asyncio.Event) -> None:
    # MCP 연결은 연 task 안에서 닫아야 해서(anyio 규칙) 시작~종료를 한 task가 맡는다
    await client.start()
    ready.set()
    await done.wait()
    await client.stop()


@pytest.fixture(scope="module")
def mcp():
    """MCP 서버를 모듈에서 1번만 띄워 모든 테스트가 같이 쓰는 fixture"""
    loop = asyncio.new_event_loop()
    client = MCPClient()
    ready, done = asyncio.Event(), asyncio.Event()
    server = loop.create_task(_serve(client, ready, done))
    loop.run_until_complete(ready.wait())

    def call(name: str, **args):
        return loop.run_until_complete(client.call_tool(name, args))

    call.client, call.loop = client, loop
    yield call
    done.set()
    loop.run_until_complete(server)
    loop.close()


def test_all_energy_tools_are_listed(mcp):
    tools = mcp.loop.run_until_complete(mcp.client.sessions["ops"].list_tools()).tools
    assert ENERGY_TOOLS <= {t.name for t in tools}


def test_own_station_card_has_verdict_and_site_checks(mcp):
    card = mcp("get_station_card", station_id="A0009172")
    assert card["is_own"] is True
    assert card["verdict"]["code"] == "fast_candidate"
    assert card["fast_1km"] == 10
    assert len(card["site_checks"]) == 4 and len(card["conversion_types"]) == 5
    assert card["snapshot"] == "2026-10-19" and card["source"]


def test_card_for_agent_has_no_verdict(mcp):
    card = mcp("get_station_card", station_id="A0009172", with_verdict=False)
    assert "verdict" not in card and "fuel_signals" not in card


def test_other_company_card_is_public_info_only(mcp):
    card = mcp("get_station_card", station_id="A0009177")    # S-OIL 홍능주유소
    assert card["is_own"] is False and "verdict" not in card


def test_card_panel_responds_within_one_second(mcp):
    started = time.perf_counter()
    mcp("get_station_card", station_id="A0001146")
    assert time.perf_counter() - started < 1.0


def test_unknown_inputs_return_error_not_exception(mcp):
    assert "error" in mcp("get_station_card", station_id="A0000000")
    assert "error" in mcp("get_fuel_demand", gu="없는구")
    assert "error" in mcp("review_cluster", gu="없는구")
    assert "error" in mcp("simulate", station_id="A0009172", fast_add=0)


def test_review_cluster_tool_matches_rules(mcp):
    cluster = mcp("review_cluster", gu="동대문구", brand="SK에너지")
    candidates = {s["station_id"] for s in cluster["stations"]
                  if s["verdict"] == "fast_candidate"}
    assert candidates == {"A0009172", "A0009248"}


def test_simulate_tool(mcp):
    result = mcp("simulate", station_id="A0009172", fast_add=2)
    assert result["after"]["fast_ids"] == 107 and result["cars_per_hour"] == 3.9


def test_resolve_station_asks_back_on_same_name(mcp):
    assert mcp("resolve_station", query="신성주유소")["ambiguous"] is True
    assert mcp("resolve_station", query="박미주유소")["candidates"][0]["station_id"] == "A0001146"


def test_search_stations_dongdaemun_sk(mcp):
    result = mcp("search_stations", gu="동대문구", brand="SK에너지")
    assert result["count"] == 8


def test_h2_status_gangseo(mcp):
    result = mcp("get_h2_status", gu="강서구")
    assert result["station_count"] == 3 and result["wait_total"] == 3


def test_map_pins_cover_all_stations_with_badges(mcp):
    result = mcp("get_map_pins", company="SK에너지")
    pins = result["pins"]
    assert len(pins) == 470
    assert sum(p["own"] for p in pins) == 130                 # SK 주유소 114 + LPG 16
    badged = {p["station_id"] for p in pins if p["badge"] == "⚡"}
    assert {"A0009172", "A0009248"} <= badged
    assert all(p["own"] for p in pins if p["badge"])          # 타사 핀에는 배지 없음
    assert result["districts"]["동대문구"]["ev_bottleneck"] is True
    assert result["districts"]["강남구"]["diesel_idle"] is True


def test_criteria_resource(mcp):
    text = mcp.loop.run_until_complete(
        mcp.client.read_resource("criteria://energy-mix", server="ops"))
    data = json.loads(text)
    assert data["rule_version"] and data["thresholds"]["fast_candidate_max"] == 2
    assert data["assumptions"]["occupancy_min_high_power"] == 30.5


def test_district_review_prompt(mcp):
    server = mcp.loop.run_until_complete(mcp.client.find_prompt("district_review_prompt"))
    text = mcp.loop.run_until_complete(mcp.client.get_prompt(
        "district_review_prompt", {"gu": "동대문구", "brand": "SK에너지"}, server=server))
    assert "동대문구" in text and "현장 확인" in text


def test_card_shows_only_own_fuel_signal(mcp):
    # 서대문구는 LPG 유휴 위험 구지만, 주유소 카드에는 경유 신호만 해당 — 경유는 아니므로 비어 있음
    card = mcp("get_station_card", station_id="A0000568")    # 서대문구 광호주유소
    assert card["fuel_signals"] == []
