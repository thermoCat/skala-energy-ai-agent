"""평가 질문 18개를 그래프 끝까지 돌려 숫자 정확도·LLM 판별 일치율을 재는 스크립트

정답 숫자는 그래프를 거치지 않고 스냅샷에서 바로 계산한다(energy 패키지 직접 호출).

사용법:
    LANGCHAIN_TRACING_V2=false .venv/bin/python3 eval/run_eval.py fake
    LANGCHAIN_TRACING_V2=false .venv/bin/python3 eval/run_eval.py ollama
출력: eval/results_<backend>.json, eval/results_<backend>.md
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.deps import SupervisorDeps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.mcp_client import MCPClient  # noqa: E402
from agent.session_store import SqliteSessionStore  # noqa: E402
from agent.validation import NUMBER, _strip_known_text, unbacked_numbers  # noqa: E402
from energy.indicators import ev_supply, fuel_demand  # noqa: E402
from energy.rules.simulate import simulate_fast_add  # noqa: E402
from energy.store import load_snapshot  # noqa: E402

SNAP = load_snapshot("2026-10-19")


def _gu_numbers(gu: str) -> list[str]:
    ev = fuel_demand(SNAP, gu)["fuels"]["전기"]["rate_pct"]
    return [f"{ev:+.1f}%", f"{ev_supply(SNAP, gu)['fast_per_10k_ev']:.1f}"]


def _scenario_numbers(station_id: str, n: int) -> list[str]:
    sim = simulate_fast_add(SNAP, station_id, n)
    return [str(sim["after"]["fast_ids"]), str(sim["cars_per_hour"])]


# (이름, 요청, 답에 꼭 있어야 할 숫자, 기대 종류)
CASES = [
    ("풍한주유소 진단", {"query": "이 주유소에 무엇을 더해야 해?", "station_id": "A0009172"},
     _gu_numbers("동대문구"), "station"),
    ("박미주유소 진단", {"query": "이 주유소에 무엇을 더해야 해?", "station_id": "A0001146"},
     _gu_numbers("금천구"), "station"),
    ("미래주유소 진단", {"query": "이 주유소에 무엇을 더해야 해?", "station_id": "A0006762"},
     _gu_numbers("광진구"), "station"),
    ("광호주유소 진단", {"query": "이 주유소에 무엇을 더해야 해?", "station_id": "A0000568"},
     _gu_numbers("서대문구"), "station"),
    ("수서지점 진단", {"query": "이 주유소에 무엇을 더해야 해?", "station_id": "A0001213"},
     _gu_numbers("강남구"), "station"),
    ("동대문구 묶음", {"query": "동대문구 SK 거점 8곳을 어떻게 나눠 써야 해?", "gu": "동대문구"},
     _gu_numbers("동대문구"), "district"),
    ("강남구 묶음", {"query": "강남구 SK 거점 어떻게 나눠 써야 해?", "gu": "강남구"},
     _gu_numbers("강남구"), "district"),
    ("성북구 묶음", {"query": "성북구 SK 거점 어떻게 나눠 써야 해?", "gu": "성북구"},
     _gu_numbers("성북구"), "district"),
    ("양천구 묶음", {"query": "양천구 SK 거점 어떻게 나눠 써야 해?", "gu": "양천구"},
     _gu_numbers("양천구"), "district"),
    ("금천구 묶음", {"query": "금천구 SK 거점 어떻게 나눠 써야 해?", "gu": "금천구"},
     _gu_numbers("금천구"), "district"),
    ("용산구 (문장만)", {"query": "용산구 SK 거점 어떻게 써야 해?"},
     _gu_numbers("용산구"), "district"),
    ("풍한 +2기", {"query": "급속 2기 넣으면?", "station_id": "A0009172"},
     _scenario_numbers("A0009172", 2), "scenario"),
    ("살피재 +3기", {"query": "급속 3기 넣으면?", "station_id": "A0000379"},
     _scenario_numbers("A0000379", 3), "scenario"),
    ("박미 +2기 (이름)", {"query": "박미주유소에 급속 2기 넣으면?"},
     _scenario_numbers("A0001146", 2), "scenario"),
    ("홍능주유소 (타사)", {"query": "이 주유소 주변 경쟁 현황은?", "station_id": "A0009177"},
     _gu_numbers("동대문구")[:1], "station"),
    ("신성주유소 (동명)", {"query": "신성주유소 어때?"}, [], "ask_user"),
    ("S-OIL 신성주유소", {"query": "S-OIL 신성주유소 어때?"}, [], "station"),
    ("GS 동작구 (재계획)", {"query": "동작구 거점 어떻게 써야 해?", "company": "GS칼텍스"},
     _gu_numbers("관악구"), "district"),
]


def _build_llms(backend: str):
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        return (OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"), 0.00015),
                OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"), 0.005))
    from agent.fake_llm import FastLLM, QualityLLM
    return FastLLM("fast-model-stub", 0.00015), QualityLLM("quality-model-stub", 0.005)


def _same(a, b) -> bool:
    return set(a or []) == set(b) if isinstance(b, list) else a == b


def score_case(name: str, expect_numbers: list[str], expect_kind: str, r: dict, sec: float) -> dict:
    """한 질문의 결과를 지표로 바꾸는 함수"""
    final, facts = r.get("final", ""), r.get("facts", [])
    numbers = NUMBER.findall(_strip_known_text(final, facts)) if facts else []
    unbacked = unbacked_numbers(final, facts) if facts else []
    baseline, llm = r.get("baseline", {}), r.get("llm_verdicts", {})
    agree = [_same(llm.get(item, {}).get("value"), base) for item, base in baseline.items()] \
        if llm else []
    kind = "ask_user" if r.get("route") == "ask_user" else r.get("request_type")
    return {
        "name": name, "seconds": round(sec, 2), "kind": kind, "kind_ok": kind == expect_kind,
        "numbers": len(numbers), "unbacked": unbacked,
        "expected_found": [n for n in expect_numbers if n in final],
        "expected_missing": [n for n in expect_numbers if n not in final],
        "judged_items": len(agree), "llm_agree": sum(agree),
        "gate_corrections": len(r.get("gate_corrections", [])),
        "validation": (r.get("validation") or {}).get("passed"),
        "llm_calls": r.get("llm_calls", 0),
    }


async def run(backend: str) -> list[dict]:
    os.environ["SESSION_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "eval.db")
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    fast, quality = _build_llms(backend)
    graph = build_graph(SupervisorDeps(fast, quality, mcp, SqliteSessionStore(), "v2"))
    rows = []
    try:
        for name, request, numbers, kind in CASES:
            state = {"thread_id": f"eval-{len(rows)}", "history": [], "company": "SK에너지",
                     "events": [], "cost_usd": 0.0, **request}
            started = time.perf_counter()
            result = await graph.ainvoke(state)
            rows.append(score_case(name, numbers, kind, result, time.perf_counter() - started))
            print(f"{name}: {rows[-1]}", flush=True)
    finally:
        await mcp.stop()
    return rows


def summarize(rows: list[dict]) -> dict:
    """질문별 지표를 합쳐 발표용 요약을 만드는 함수"""
    numbers = sum(r["numbers"] for r in rows)
    unbacked = sum(len(r["unbacked"]) for r in rows)
    expected = sum(len(r["expected_found"]) + len(r["expected_missing"]) for r in rows)
    judged = sum(r["judged_items"] for r in rows)
    return {
        "cases": len(rows),
        "kind_ok": sum(r["kind_ok"] for r in rows),
        "number_accuracy_pct": round((numbers - unbacked) / numbers * 100, 1) if numbers else None,
        "numbers": numbers, "unbacked": unbacked,
        "expected_found": sum(len(r["expected_found"]) for r in rows), "expected_total": expected,
        "llm_agreement_pct": round(sum(r["llm_agree"] for r in rows) / judged * 100, 1)
        if judged else None,
        "judged_items": judged, "gate_corrections": sum(r["gate_corrections"] for r in rows),
        "validation_passed": sum(1 for r in rows if r["validation"] in (True, None)),
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2],
    }


def to_markdown(backend: str, rows: list[dict], summary: dict) -> str:
    lines = [f"# 평가 결과 — {backend}", "",
             f"- 질문 {summary['cases']}개, 요청 분류 정답 {summary['kind_ok']}개",
             f"- 답변 숫자 정확도 {summary['number_accuracy_pct']}% "
             f"(숫자 {summary['numbers']}개 중 근거 없음 {summary['unbacked']}개)",
             f"- 정답 숫자 포함 {summary['expected_found']}/{summary['expected_total']}",
             f"- LLM 판별 일치율(게이트 교정 전) {summary['llm_agreement_pct']}% "
             f"(판별 항목 {summary['judged_items']}개, 게이트 교정 {summary['gate_corrections']}건)",
             f"- 검증 합격 {summary['validation_passed']}/{summary['cases']}, "
             f"응답 시간 중앙값 {summary['median_seconds']}초", "",
             "| 질문 | 종류 | 숫자 | 근거 없음 | 정답 숫자 | 판별 일치 | 교정 | 검증 | 초 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        exp = f"{len(r['expected_found'])}/{len(r['expected_found']) + len(r['expected_missing'])}"
        lines.append(f"| {r['name']} | {r['kind']}{'' if r['kind_ok'] else ' ✗'} | {r['numbers']} | "
                     f"{len(r['unbacked'])} | {exp} | {r['llm_agree']}/{r['judged_items']} | "
                     f"{r['gate_corrections']} | {r['validation']} | {r['seconds']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    backend = sys.argv[1] if len(sys.argv) > 1 else "fake"
    rows = asyncio.run(run(backend))
    summary = summarize(rows)
    out = ROOT / "eval"
    (out / f"results_{backend}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"results_{backend}.md").write_text(to_markdown(backend, rows, summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
