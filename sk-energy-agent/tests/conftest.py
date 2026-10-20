"""그래프 테스트 공통 fixture — 실제 MCP 서버 + 실데이터 스냅샷 + fake LLM"""
import asyncio

import pytest

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore


@pytest.fixture
def store(tmp_path):
    """테스트마다 새로 만드는 대화·판별 이력 저장소"""
    s = SqliteSessionStore(tmp_path / "test.db")
    yield s
    s.close()


async def _ask(store, query: str, wrap_mcp, quality, **state) -> dict:
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    try:
        deps = SupervisorDeps(FastLLM("fast", 0.0), quality or QualityLLM("quality", 0.0),
                              wrap_mcp(mcp) if wrap_mcp else mcp, store, "v2")
        return await build_graph(deps).ainvoke(
            {"query": query, "thread_id": "t", "history": [], "company": "SK에너지",
             "events": [], "cost_usd": 0.0, **state})
    finally:
        await mcp.stop()


@pytest.fixture
def ask(store):
    """질문 1건을 그래프 끝까지 실행하는 함수를 돌려주는 fixture

    ask("질문", station_id=..., gu=..., company=..., wrap_mcp=..., quality=...)
    """
    def run(query: str, wrap_mcp=None, quality=None, **state) -> dict:
        return asyncio.run(_ask(store, query, wrap_mcp, quality, **state))
    return run


def event_types(result: dict) -> list[str]:
    return [e["type"] for e in result["events"]]
