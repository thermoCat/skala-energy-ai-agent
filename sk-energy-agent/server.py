"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import json
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"
    company: str = "SK에너지"
    station_id: str | None = None   # 지도 핀 클릭 시 프론트가 채움 (동명 주유소 오인 방지)
    gu: str | None = None


def _initial_state(message: str, thread_id: str, history: list[dict], company: str,
                   station_id: str | None, gu: str | None) -> dict:
    """그래프 첫 상태를 만드는 함수

    Args:
        message: 사용자 질문
        thread_id: 대화 id
        history: 이전 대화
        company: 로그인 회사
        station_id: 지도에서 고른 주유소 번호 (없으면 None)
        gu: 지도에서 고른 자치구 (없으면 None)
    Returns:
        그래프 입력 dict
    """
    return {"query": message, "thread_id": thread_id, "history": history,
            "company": company, "station_id": station_id, "gu": gu,
            "events": [], "cost_usd": 0.0}


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    mcp = MCPClient(flaky_first_tool_call=False)   # 베이스 Wave 9 데모용 강제 실패는 끔
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    backend, fast_llm, quality_llm = _build_llms()
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
    )
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name}


@app.get("/version")
async def version():
    return {"supervisor_version": app.state.deps.version}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "request_type": result.get("request_type"),
        "station_id": result.get("station_id"),
        "gu": result.get("gu"),
        "gate_corrections": len(result.get("gate_corrections", [])),
        "validation_passed": (result.get("validation") or {}).get("passed"),
        "replanned_from": result.get("replanned_from"),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
    }


@traceable(name="chat_request")
async def _handle_chat(req: ChatRequest) -> tuple[dict, str | None]:
    deps = app.state.deps
    message, thread_id = req.message, req.thread_id
    history = deps.session_store.load(thread_id)   # Wave 12
    if deps.version == "v2":
        result = await app.state.graph.ainvoke(_initial_state(
            message, thread_id, history, req.company, req.station_id, req.gu))
    else:
        result = await run_v1(deps, message, thread_id, history)   # Wave 14
    deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", result["final"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req)
    return {"reply": result["final"], "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default", company: str = "SK에너지",
                      station_id: str | None = None, gu: str | None = None):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps

    async def gen():
        history = deps.session_store.load(thread_id)
        if deps.version != "v2":
            result = await run_v1(deps, message, thread_id, history)
            for ev in result["events"]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': result['final']}, ensure_ascii=False)}\n\n"
        else:
            seen = 0
            final = ""
            last_prompt = None   # finalize까지 살아남은 최종 write/chat 호출의 실제 프롬프트
            # stream_mode를 리스트로 주면 (mode, payload) 튜플이 나온다 — "updates"는
            # 노드가 끝날 때마다의 상태 델타, "custom"은 write_node/chat_answer_node가
            # get_stream_writer()로 실행 중에 즉시 흘려보내는 토큰(AnswerToken) 이벤트다.
            async for mode, payload in app.state.graph.astream(
                    _initial_state(message, thread_id, history, company, station_id, gu),
                    stream_mode=["updates", "custom"]):
                if mode == "custom":
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    continue
                for node_name, delta in payload.items():
                    for ev in (delta.get("events") or [])[seen:]:
                        ev = {**ev, "node": node_name}
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if delta.get("events"):
                        seen = len(delta["events"])
                    if delta.get("final"):
                        final = delta["final"]
                    if delta.get("last_prompt"):
                        last_prompt = delta["last_prompt"]
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': final, 'prompt': last_prompt}, ensure_ascii=False)}\n\n"
            deps.session_store.append(thread_id, "user", message)
            deps.session_store.append(thread_id, "assistant", final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── 대시보드 API (LLM 없음) — 에이전트와 같은 MCP Tool을 불러 숫자를 맞춘다 ──────────

async def _tool(name: str, args: dict) -> dict:
    """MCP Tool을 불러 응답을 돌려주는 함수 (Tool이 error 를 돌려주면 404)

    Args:
        name: Tool 이름
        args: Tool 입력
    Returns:
        Tool 응답 dict
    Raises:
        HTTPException: 없는 주유소·자치구 등 Tool이 error 를 돌려줄 때
    """
    data = await app.state.deps.mcp.call_tool(name, args)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@app.get("/api/pins")
async def api_pins(company: str = "SK에너지", brand: str | None = None):
    """지도 핀 470개와 자치구 색칠 값 (화면을 열 때 1번)"""
    return await _tool("get_map_pins", {"company": company, "brand": brand})


@app.get("/api/stations/resolve")
async def api_resolve(q: str, brand: str | None = None):
    """이름으로 주유소 찾기 (검색창) — 같은 이름이 여러 곳이면 ambiguous=true"""
    return await _tool("resolve_station", {"query": q, "brand": brand})


@app.get("/api/stations/{station_id}/card")
async def api_station_card(station_id: str, company: str = "SK에너지"):
    """정보 패널 카드 — 자사면 기준 판정·현장 확인 항목, 타사면 공개 정보만"""
    return await _tool("get_station_card", {"station_id": station_id, "company": company})


@app.get("/api/stations/{station_id}/simulate")
async def api_simulate(station_id: str, fast_add: int = Query(ge=1), kw: int = 100,
                       company: str = "SK에너지"):
    """급속 N기 추가 시나리오 숫자 (자사 거점만 — 타사 투자 계산은 하지 않음)"""
    card = await _tool("get_station_card", {"station_id": station_id, "company": company,
                                            "with_verdict": False})
    if not card["is_own"]:
        raise HTTPException(status_code=403, detail={"message": "타사 거점은 시나리오를 계산하지 않음"})
    return await _tool("simulate", {"station_id": station_id, "fast_add": fast_add, "kw": kw})


@app.get("/api/districts/{gu}")
async def api_district(gu: str, company: str = "SK에너지"):
    """자치구 패널 — 5개 연료 수요, 충전 공급, 수소 대기, 자사 거점 목록"""
    demand = await _tool("get_fuel_demand", {"gu": gu})
    supply = await _tool("get_ev_supply", {"gu": gu})
    h2 = await _tool("get_h2_status", {"gu": gu})
    own = await _tool("search_stations", {"gu": gu, "brand": company})
    return {"gu": gu, "company": company, "demand": demand, "supply": supply, "h2": h2,
            "own_stations": own["stations"]}


@app.get("/api/clusters/{gu}")
async def api_cluster(gu: str, company: str = "SK에너지"):
    """자사 묶음 화면 — 거점별 기준 판정(⚡/유지)·연료 신호·전환 유형·현장 확인 항목"""
    return await _tool("review_cluster", {"gu": gu, "brand": company})


@app.get("/api/criteria")
async def api_criteria():
    """판별 기준 임계값·가정값·규칙 버전 (criteria://energy-mix)"""
    text = await app.state.deps.mcp.read_resource("criteria://energy-mix", server="ops")
    return json.loads(text)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
