"""판단 이력 테스트 — 사람이 남긴 결정이 다음 답에 이어지는지, 에이전트가 쓸 수 없는지"""
import asyncio

from agent.mcp_client import MCPClient
from agent.session_store import read_decision_log


def _record(store, station_id="A0009172", gu="동대문구", company="SK에너지"):
    return store.record_human_decision(company, station_id, gu, "보류",
                                       "소형 추가 (초급속 충전기 1~2대)", "부지 협의 필요", "담당자")


def test_previous_decision_appears_in_station_answer(ask, store):
    before = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")
    _record(store)
    after = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")

    assert "지난 결정" not in before["final"]
    assert "지난 결정 — 풍한주유소" in after["final"] and "부지 협의 필요" in after["final"]
    assert any(f["key"] == "decision:A0009172" for f in after["facts"])
    assert after["verdicts"] == before["verdicts"]       # 결정은 판정을 바꾸지 않는다
    assert after["validation"]["passed"] is True


def test_previous_decision_appears_in_district_answer(ask, store):
    _record(store)
    result = ask("동대문구 SK 거점 어떻게 나눠 써야 해?", gu="동대문구")
    assert "지난 결정 — 풍한주유소" in result["final"]


def test_other_company_cannot_see_decisions(store, tmp_path):
    _record(store)
    db = tmp_path / "test.db"
    assert read_decision_log(db, "GS칼텍스", station_id="A0009172")["human"] == []
    assert len(read_decision_log(db, "SK에너지", gu="동대문구")["human"]) == 1


def test_agent_has_no_tool_that_writes_decisions():
    async def tool_names():
        mcp = MCPClient()
        await mcp.start()
        try:
            return {t.name for t in (await mcp.sessions["ops"].list_tools()).tools}
        finally:
            await mcp.stop()
    names = asyncio.run(tool_names())
    assert "get_decision_log" in names
    assert not any("record" in n or "write" in n for n in names)
