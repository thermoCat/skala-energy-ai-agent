"""그래프 테스트 공통 fixture — 실제 MCP 서버 + 실데이터 스냅샷 + fake LLM"""
import asyncio
import os

import pytest

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore

# 일간·주간 추적 기대값은 10-19 11시 ~ 10-21 9시 수집 4회 기준 — 새 수집이 쌓여도 테스트가 흔들리지 않게 고정
# (MCP 서버 subprocess 도 이 환경변수를 물려받는다)
os.environ.setdefault("SNAPSHOT_UNTIL", "2026-10-21T09")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """테스트마다 새로 만드는 대화·판단 이력 저장소 (MCP 서버도 같은 파일을 읽게 경로를 맞춤)"""
    db = tmp_path / "test.db"
    monkeypatch.setenv("SESSION_DB_PATH", str(db))
    s = SqliteSessionStore(db)
    yield s
    s.close()


async def _ask(store, query: str, wrap_mcp, quality, **state) -> dict:
    mcp = MCPClient()
    await mcp.start()
    try:
        deps = SupervisorDeps(FastLLM("fast", 0.0), quality or QualityLLM("quality", 0.0),
                              wrap_mcp(mcp) if wrap_mcp else mcp, store)
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
