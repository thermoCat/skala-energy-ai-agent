"""주유소 핀 클릭 흐름 뼈대 테스트 — 실제 MCP 서버 + 실데이터 스냅샷 + fake LLM"""
import asyncio

import pytest

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore

PUNGHAN = "A0009172"    # 동대문구 SK에너지 풍한주유소
HONGNEUNG = "A0009177"  # 동대문구 S-OIL 홍능주유소 (타사)


async def _ask(store: SqliteSessionStore, station_id: str, message: str) -> dict:
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    try:
        deps = SupervisorDeps(FastLLM("fast", 0.0), QualityLLM("quality", 0.0),
                              mcp, store, "v2")
        return await build_graph(deps).ainvoke(
            {"query": message, "thread_id": "t", "history": [], "company": "SK에너지",
             "station_id": station_id, "events": [], "cost_usd": 0.0})
    finally:
        await mcp.stop()


@pytest.fixture
def store(tmp_path):
    s = SqliteSessionStore(tmp_path / "test.db")
    yield s
    s.close()


def test_own_station_gate_corrects_diesel_judgement(store):
    result = asyncio.run(_ask(store, PUNGHAN, "이 주유소에 무엇을 더해야 해?"))

    assert result["route"] == "station"
    assert result["gu"] == "동대문구"
    assert result["llm_verdicts"]["diesel_idle"]["value"] is True    # fake의 의도적 오판
    assert result["verdicts"]["ev_bottleneck"]["value"] is True
    assert result["verdicts"]["diesel_idle"]["value"] is False       # 게이트가 교정
    assert result["gate_corrections"] == [
        {"item": "diesel_idle", "llm": True, "baseline": False}]
    assert any(e["type"] == "GateCorrected" for e in result["events"])

    # 분석 에이전트 입력(사실)에 기준 판정 값이 섞이지 않음
    assert all(f["key"] not in ("ev_bottleneck", "diesel_idle") for f in result["facts"])

    # 답변 숫자는 Tool 결과와 같은 값
    answer = result["final"]
    for text in ("동대문구", "풍한주유소", "+57.0%", "+43.2%", "317.1", "583.8",
                 "-4.8%", "-6.6%", "게이트 교정", "현장 확인 항목"):
        assert text in answer

    logs = store.load_decisions(PUNGHAN)
    assert len(logs) == 1 and logs[0]["corrected"] == 1


def test_same_question_three_times_gives_same_final_verdict(store):
    finals = [asyncio.run(_ask(store, PUNGHAN, "이 주유소에 무엇을 더해야 해?"))["verdicts"]
              for _ in range(3)]
    assert finals[0] == finals[1] == finals[2]


def test_other_company_station_gets_public_info_only(store):
    result = asyncio.run(_ask(store, HONGNEUNG, "이 주유소 주변 경쟁 현황은?"))

    assert result["is_own"] is False
    assert "llm_verdicts" not in result and "verdicts" not in result
    assert "투자 판별은 하지 않습니다" in result["final"]
    assert store.load_decisions(HONGNEUNG) == []


def test_unknown_station_id_does_not_crash(store):
    result = asyncio.run(_ask(store, "A0000000", "이 주유소에 무엇을 더해야 해?"))
    assert "찾지 못했습니다" in result["final"]
