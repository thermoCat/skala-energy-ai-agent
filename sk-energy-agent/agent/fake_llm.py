"""Wave 4/10a — 결정론적 Fake LLM 2종 (fast/quality) + 실측 비용 계산.

실제 LLM 호출 없이도 파이프라인 전체가 결정론적으로 재현되도록, 프롬프트 내용에 따라
정해진 응답을 돌려준다. 각 호출은 LangSmith에 run_type="llm"으로 기록되고, 문자열
길이 기반 토큰 추정치로 비용을 계산해 상태에 누적한다 (Wave 10a의 모델 분리 근거).
"""
import re

from langsmith import traceable


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float):
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """분류·계획 등 짧고 정형적인 단계 전담 (저비용)."""

    @traceable(run_type="llm", name="fast_llm.route")
    def route(self, query: str) -> tuple[dict, dict]:
        """Wave 5 — 쿼리 라우팅. 장애 관련이면 incident 파이프라인, 아니면 chat 직행."""
        incident = bool(re.search(r"장애|진단|다운|지연|급증|incident|티켓", query))
        result = {"route": "incident" if incident else "chat", "needs_analysis": False}
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.plan")
    def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        """Wave 4 — Plan/Task 분해. MCP Prompt가 준 표준 절차를 따라 계획을 세운다.

        Wave 6 재현: needs_analysis=False라고 스스로 판단해 놓고도 analysis task를
        끼워 넣는 '모델의 자기모순'을 결정론적으로 재현한다 → gate 노드가 걸러낸다.
        """
        systems = re.findall(r"[a-z][a-z0-9-]+-(?:service|\d+)", query) or ["auth-service"]
        if "web-01" not in systems:
            systems.append("web-01")   # 연관 시스템 비교 조회 → Wave 10b 병렬 실행 대상
        tasks = [
            {"id": f"task-{i}", "agent": "research", "kind": "monitoring",
             "target": s, "status": "pending"}
            for i, s in enumerate(dict.fromkeys(systems))
        ]
        tasks.append({"id": "task-analysis", "agent": "analysis",
                      "kind": "pattern-analysis", "status": "pending"})   # 자기모순 task
        result = {"needs_analysis": False, "tasks": tasks}
        return result, self._usage(query + procedure_prompt, str(result))

    @traceable(run_type="llm", name="fast_llm.analyze")
    def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        """Wave 6 보완 — 여러 시스템의 monitoring 결과 사이에 공통 패턴이 있는지 비교."""
        mon = [c["data"] for c in collected if c["source"] == "monitoring"]
        degraded = [d["system_name"] for d in mon if d.get("status") != "healthy"]
        if len(degraded) >= 2:
            summary = (f"{', '.join(degraded)} 복수 시스템이 동시에 저하됨 — "
                       f"공유 의존성 등 공통 원인이 의심됨")
        elif degraded:
            summary = f"{degraded[0]}만 단독 저하 — 연관 시스템은 정상, 국소 이슈로 추정"
        else:
            summary = "모니터링된 시스템 모두 healthy — 패턴상 이상 징후 없음"
        return summary, self._usage(str(collected), summary)

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """Wave 5의 direct 경로 + Wave 12 증명용 — 히스토리에서 이름을 기억해낸다.

        fake 백엔드는 실제 네트워크 호출이 없어 토큰 단위로 쪼갤 대상이 없다 —
        ollama_llm.py와 인터페이스(on_token 콜백)만 맞추고 결과를 한 번에 반환한다."""
        for msg in reversed(history):
            m = re.search(r"제 이름은 (\S+?)입니다", msg.get("content", ""))
            if m and re.search(r"이름이 뭐", query):
                reply = f"{m.group(1)}님이라고 말씀하셨어요. 무엇을 도와드릴까요?"
                if on_token is not None:
                    on_token(reply)
                return reply, self._usage(query, reply)
        m = re.search(r"제 이름은 (\S+?)입니다", query)
        if m:
            reply = f"안녕하세요, {m.group(1)}님! 무엇을 도와드릴까요?"
        else:
            reply = "안녕하세요! 무엇을 도와드릴까요?"
        if on_token is not None:
            on_token(reply)
        return reply, self._usage(query, reply)


class QualityLLM(FakeLLM):
    """최종 답변 작성 전담 (고품질·고비용)."""

    @traceable(run_type="llm", name="quality_llm.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
              feedback: str | None, on_token=None) -> tuple[str, dict]:
        """Wave 8 재현: 1차 초안은 의도적으로 저품질(너무 짧음) → validator가 반려하면
        피드백을 반영한 구조화된 2차 초안을 내놓는다.

        fake 백엔드는 실제 네트워크 호출이 없어 토큰 단위로 쪼갤 대상이 없다 —
        ollama_llm.py와 인터페이스(on_token 콜백)만 맞추고 결과를 한 번에 반환한다."""
        if attempt == 1:
            draft = "재시작하면 됩니다."
            if on_token is not None:
                on_token(draft)
            return draft, self._usage(query, draft)

        mon = [c for c in collected if c["source"] == "monitoring"]
        run = [c for c in collected if c["source"] == "runbook"]
        summary = " · ".join(
            f"{c['data']['system_name']} 상태={c['data']['status']}, "
            f"CPU {c['data']['cpu_percent']}% (알림 {len(c['data']['alerts'])}건)"
            for c in mon)
        lines = [f"'{query}' 진단 결과:", f"1) 모니터링: {summary}"]
        if run:
            body_lines = [l.strip() for l in run[0]["data"].strip().splitlines() if l.strip()]
            title = body_lines[0].lstrip("# ")
            steps = [m.group(1) for l in body_lines[1:]
                     if (m := re.match(r"^\d+\.\s+(.*)", l))]
            advice = " ".join(steps[1:3]) or "표준 절차를 따라 조치하세요."
            lines.append(f"2) 표준 절차: '{title}'에 따라 {advice}")
        lines.append("3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.")
        draft = " ".join(lines)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(query + str(feedback), draft)

    # ── 에너지 도메인 (주유소 핀 클릭 흐름) ──────────────────────────────

    @traceable(run_type="llm", name="quality_llm.judge_station")
    def judge_station(self, facts: list[dict]) -> tuple[dict, dict]:
        """분석 에이전트 판별(fake) — 사실 [F]만 보고 전기 병목·경유 유휴 위험을 판별하는 함수

        의도적 오판 1건: 경유는 서울 평균과 비교하지 않고 "줄었으면 유휴 위험"으로 판별한다.
        동대문구(-4.8%, 서울 -6.6%)에서 권위 게이트가 이를 교정하는 장면이 매번 재현된다.

        Args:
            facts: 번호가 붙은 사실 목록 ({"id", "key", "value", ...})
        Returns:
            (항목별 {"value", "facts", "reason"} 판별 dict, 사용량)
        """
        by_key = {f["key"]: f for f in facts}

        def value(key):
            return by_key[key]["value"] if key in by_key else None

        def ids(*keys):
            return [by_key[k]["id"] for k in keys if k in by_key]

        growth, seoul_growth = value("ev_growth_pct"), value("seoul_ev_growth_pct")
        density, seoul_density = value("fast_per_10k_ev"), value("seoul_fast_per_10k_ev")
        diesel = value("diesel_rate_pct")

        ev_known = None not in (growth, seoul_growth, density, seoul_density)
        ev = ev_known and growth >= seoul_growth and density <= seoul_density
        result = {
            "ev_bottleneck": {
                "value": ev if ev_known else None,
                "facts": ids("ev_growth_pct", "seoul_ev_growth_pct",
                             "fast_per_10k_ev", "seoul_fast_per_10k_ev"),
                "reason": "전기차 증가가 서울보다 빠르고 급속 밀도는 서울보다 낮음"
                          if ev else "증가 속도나 급속 밀도가 서울 평균보다 나쁘지 않음",
            },
            "diesel_idle": {
                "value": diesel < 0 if diesel is not None else None,
                "facts": ids("diesel_rate_pct"),
                "reason": "경유 차량이 1년 사이 줄었음",
            },
        }
        return result, self._usage(str(facts), str(result))

    @traceable(run_type="llm", name="quality_llm.write_station")
    async def write_station(self, facts: list[dict], verdicts: dict,
                            corrections: list[dict], is_own: bool,
                            site_checks: tuple[str, ...], item_labels: dict,
                            on_token=None) -> tuple[str, dict]:
        """작성 에이전트(fake) — 사실 [F]와 확정 판정 [J]로 주유소 답변을 쓰는 함수

        Args:
            facts: 번호가 붙은 사실 목록
            verdicts: 게이트 확정 판정 (항목 → {"id", "value"})
            corrections: 게이트가 교정한 항목 목록
            is_own: 자사 거점 여부 (타사면 공개 정보만)
            site_checks: 현장 확인 항목
            item_labels: 판정 항목 키 → 화면 이름
            on_token: 토큰 스트리밍 콜백
        Returns:
            (답변 문자열, 사용량)
        """
        by_key = {f["key"]: f for f in facts}

        def cite(key, fmt="{}"):
            if key not in by_key:
                return "데이터 없음"
            return fmt.format(by_key[key]["value"]) + f"[{by_key[key]['id']}]"

        station = by_key["station"]["value"] if "station" in by_key else "주유소"
        lines = [f"[{station}]"]
        ev_line = (f"전기차 1년 {cite('ev_growth_pct', '{:+.1f}%')} "
                   f"(서울 {cite('seoul_ev_growth_pct', '{:+.1f}%')}), "
                   f"전기차 1만 대당 급속 ID {cite('fast_per_10k_ev', '{:.1f}')} "
                   f"(서울 {cite('seoul_fast_per_10k_ev', '{:.1f}')})")

        if not is_own:
            lines += [
                "공개 정보입니다.",
                f"- 주유소 안 급속충전기 {cite('in_station_fast_ids', '{}개')}",
                f"- 자치구 {ev_line}",
                "타사 거점이라 투자 판별은 하지 않습니다.",
            ]
        else:
            ev = verdicts.get("ev_bottleneck", {})
            diesel = verdicts.get("diesel_idle", {})
            ev_text = {True: "전기 병목 자치구입니다", False: "전기 병목 자치구가 아닙니다"}
            diesel_text = {True: "서울 평균보다 더 줄어 유휴 위험 표시 대상입니다",
                           False: "서울 평균보다 덜 줄어 유휴 위험이 아닙니다"}
            lines += [
                "확정 판정 기준 진단입니다.",
                f"1) 전기: {ev_line} → "
                f"{ev_text.get(ev.get('value'), '판별 불가(데이터 없음)')}[{ev.get('id', 'J?')}].",
                f"2) 경유: 1년 {cite('diesel_rate_pct', '{:+.1f}%')} "
                f"(서울 {cite('seoul_diesel_rate_pct', '{:+.1f}%')}) → "
                f"{diesel_text.get(diesel.get('value'), '판별 불가(데이터 없음)')}"
                f"[{diesel.get('id', 'J?')}].",
                f"3) 이 주유소: 주유소 안 급속충전기 {cite('in_station_fast_ids', '{}개')}. "
                "반경 1km 급속 비교로 정하는 급속 후보 여부는 아직 판별하지 않았습니다.",
            ]
            for c in corrections:
                label = item_labels.get(c["item"], c["item"])
                yes_no = lambda v: {True: "예", False: "아니오"}.get(v, "판별 없음")
                lines.append(f"※ 게이트 교정: 분석 에이전트 판별 '{label}: "
                             f"{yes_no(c['llm'])}' → 기준에 따라 "
                             f"'{yes_no(c['baseline'])}'로 바로잡았습니다.")
            lines.append("현장 확인 항목: " + " · ".join(site_checks))
            lines.append("이 답변은 투자 결정이 아니라 진단입니다. 결정은 담당자가 합니다.")

        draft = "\n".join(lines)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(str(facts) + str(verdicts), draft)
