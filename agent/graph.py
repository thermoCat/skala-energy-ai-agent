"""에너지 허브 Supervisor 그래프 (LangGraph StateGraph)

route(Supervisor) ─┬─ 일반 대화 → chat_answer
                   ├─ 이름만 말함 → resolve ─(같은 이름 여러 곳)→ ask_user
                   ├─ 주유소·자치구·시나리오 → energy_research ⇄ replan → analyst → authority_gate
                   │       → (후보 2곳 이상) prioritize → energy_write ⇄ energy_validate → finalize
                   └─ 상시 작업 → job_check ─(변화 없음, LLM 0회)→ job_deliver
                                            └→ analyst | policy → authority_gate → … → job_deliver

모든 노드는 SupervisorDeps 를 functools.partial 로 주입받는 함수이고, @traceable 로
LangSmith 에 계층 트리로 기록된다.
"""
from functools import partial
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from . import energy_nodes as energy
from .deps import SupervisorDeps


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    route: str
    parallel_elapsed: float
    draft: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    # 에너지 허브 진단
    company: str                 # 로그인 회사 (예: "SK에너지")
    station_id: str | None       # 지도 핀 클릭 시 프론트가 채움
    gu: str | None               # 지도 자치구 클릭 시 프론트가 채움
    request_type: str            # station | district | scenario | watch | weekly
    fast_add: int | None         # 시나리오: 더할 급속 수
    kw: int
    station_name: str | None     # 문장 속 주유소 이름 (이름 찾기용)
    brand_hint: str | None
    candidates: list[dict]       # 같은 이름 주유소 후보 (되묻기용)
    is_own: bool                 # 자사 거점 여부 (타사면 판별하지 않음)
    tool_results: dict           # Tool 응답 원본 (검증 대조용)
    errors: list[dict]           # 조회에 실패한 Tool
    own_station_count: int
    replan_count: int
    replanned_from: str | None
    facts: list[dict]            # 번호 붙은 사실 [F1..Fn]
    llm_verdicts: dict           # 분석 에이전트 판별
    baseline: dict               # 코드 기준 판정 (게이트 전용, 분석 에이전트에게 주지 않음)
    verdicts: dict               # 확정 판정 (gate 노드만 씀)
    gate_corrections: list[dict]
    priority: dict               # 충전기 후보 순서·이유 (AI 판단, 기준 없음)
    validation: dict             # {"passed", "issues"}
    rewrite_count: int
    # 상시 작업
    job: str                     # "daily_watch" | "weekly" (없으면 채팅 요청)
    started_at: str
    llm_calls: int               # 이번 실행의 LLM 호출 수
    alert_id: int | None
    job_run_id: int


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Supervisor — 상시 작업은 코드로, 에너지 질문은 분류해서, 나머지는 일반 대화로 보내는 노드"""
    if state.get("job") in energy.JOBS:
        return energy.start_job(state)
    routed = energy.energy_route(state, deps)
    if routed is not None:
        return routed
    event = {"type": "RouteDecided", "route": "chat"}
    return {"route": "chat", "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """에너지 질문이 아닐 때 대화 이력으로 짧게 답하는 노드 (토큰 스트리밍)"""
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": round(state.get("cost_usd", 0.0) + usage["usd"], 6),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat"}]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    return {"final": state["draft"],
            "events": state["events"] + [{"type": "Finalize", "pipeline": "energy-v1"}]}


def _after_route(state: AgentState) -> str:
    return {"chat": "chat_answer", "energy": "energy_research", "resolve": "resolve",
            "job": "job_check"}[state["route"]]


def build_graph(deps: SupervisorDeps):
    """그래프를 조립해 컴파일하는 함수

    Args:
        deps: 노드에 주입할 의존성 묶음 (LLM 2개·MCP·이력 저장소)
    Returns:
        컴파일된 LangGraph
    """
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("resolve", node(energy.resolve_node))
    g.add_node("ask_user", node(energy.ask_user_node))
    g.add_node("energy_research", node(energy.energy_research_node))
    g.add_node("replan", node(energy.replan_node))
    g.add_node("analyst", node(energy.analyst_node))
    g.add_node("policy", node(energy.policy_node))
    g.add_node("authority_gate", node(energy.authority_gate_node))
    g.add_node("prioritize", node(energy.prioritize_node))
    g.add_node("energy_write", node(energy.energy_write_node))
    g.add_node("energy_validate", node(energy.energy_validate_node))
    g.add_node("finalize", node(finalize_node))
    g.add_node("job_check", node(energy.job_check_node))
    g.add_node("job_deliver", node(energy.job_deliver_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "resolve": "resolve",
                             "energy_research": "energy_research", "job_check": "job_check"})
    g.add_edge("chat_answer", END)
    g.add_conditional_edges("resolve", energy.after_resolve,
                            {"energy_research": "energy_research", "ask_user": "ask_user"})
    g.add_edge("ask_user", END)
    g.add_conditional_edges("energy_research", energy.after_research,
                            {"replan": "replan", "analyst": "analyst",
                             "energy_write": "energy_write"})
    g.add_edge("replan", "energy_research")
    g.add_conditional_edges("job_check", energy.after_job_check,
                            {"analyst": "analyst", "policy": "policy",
                             "job_deliver": "job_deliver"})
    g.add_edge("analyst", "authority_gate")
    g.add_edge("policy", "authority_gate")
    g.add_conditional_edges("authority_gate", energy.after_gate,
                            {"prioritize": "prioritize", "energy_write": "energy_write"})
    g.add_edge("prioritize", "energy_write")
    g.add_edge("energy_write", "energy_validate")
    g.add_conditional_edges("energy_validate", energy.after_validate,
                            {"energy_write": "energy_write", "finalize": "finalize",
                             "job_deliver": "job_deliver"})
    g.add_edge("finalize", END)
    g.add_edge("job_deliver", END)
    return g.compile()
