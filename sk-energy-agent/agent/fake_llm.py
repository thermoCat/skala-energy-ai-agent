"""결정론 fake LLM 2종 (fast/quality) — 실제 모델 없이 같은 질문에 항상 같은 답

규칙으로 분류·판별·작성을 흉내 내고, 문자열 길이로 토큰·비용을 추정해 쌓는다.
분석 판별은 경유 유휴 위험 1건을 일부러 틀려, 권위 게이트 교정 장면이 매번 재현된다.
"""
import re

from langsmith import traceable

from energy.seoul import BRAND_ALIASES

from .answer_template import EnergyAnswer
from .energy_prompts import station_word


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float):
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """fast 모델 역할: Supervisor 분류, 정책·경쟁 판별, 답변 판정, 일반 대화 (저비용)"""

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """일반 대화(fake) — 대화 이력에서 사용자가 밝힌 이름을 기억해 답하는 함수

        Args:
            query: 사용자 메시지
            history: 이전 대화
            on_token: 토큰 콜백 (fake 는 한 번에 보냄)
        Returns:
            (답, 사용량)
        """
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

    # ── 에너지 도메인 ──────────────────────────────────────────────

    @traceable(run_type="llm", name="fast_llm.classify_energy")
    def classify_energy(self, query: str, gu_names: tuple, brand_names: tuple) -> tuple[dict, dict]:
        """Supervisor 분류(fake) — 질문에서 자치구·주유소 이름·회사·급속 N기를 뽑는 함수

        Args:
            query: 사용자 질문
            gu_names: 서울 자치구 이름 목록
            brand_names: 질문에 나올 수 있는 회사 표현 목록
        Returns:
            ({"gu", "station_name", "brand", "fast_add", "kw"}, 사용량)
        """
        gu = next((g for g in sorted(gu_names, key=len, reverse=True) if g in query), None)
        station_name = station_word(query)
        brand = next((b for b in sorted(brand_names, key=len, reverse=True) if b in query), None)
        fast = re.search(r"급속\s*(\d+)\s*기", query)
        kw = re.search(r"(\d+)\s*kW", query, re.IGNORECASE)
        result = {"gu": gu, "station_name": station_name,
                  "brand": BRAND_ALIASES.get(brand) if brand else None,
                  "fast_add": int(fast.group(1)) if fast else None,
                  "kw": int(kw.group(1)) if kw else None}
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.judge_competition")
    def judge_competition(self, facts: list[dict]) -> tuple[dict, dict]:
        """정책·경쟁 에이전트 판별(fake) — 자사 거점 1km 안 변화가 있으면 보고, 가까운 순으로 거점을 고르는 함수

        Args:
            facts: 번호 붙은 사실 목록
        Returns:
            ({"report_needed", "watch_competitors"}, 사용량)
        """
        distances = sorted((f for f in facts if f["key"].startswith("distance:")),
                           key=lambda f: f["value"])
        stations = list(dict.fromkeys(f["station_id"] for f in distances))[:3]
        result = {
            "report_needed": {"value": bool(distances), "facts": [f["id"] for f in distances],
                              "reason": "자사 거점 1km 안에 급속 신설·철거가 있음"},
            "watch_competitors": {"value": stations, "facts": [f["id"] for f in distances[:3]],
                                  "reason": "변화가 가까운 순", "reasons": {}},
        }
        return result, self._usage(str(facts), str(result))

    @traceable(run_type="llm", name="fast_llm.judge_answer")
    def judge_answer(self, draft: str, request_type: str, is_own: bool) -> tuple[dict, dict]:
        """검증 에이전트 LLM-as-Judge(fake) — 근거 표기·현장 확인·투자 결정 아님 문구를 보는 함수

        Args:
            draft: 답변 초안
            request_type: 요청 종류
            is_own: 자사 거점 여부
        Returns:
            ({"passed", "issues"}, 사용량)
        """
        issues = []
        if "[F" not in draft:
            issues.append("근거 번호 [F]가 없음")
        if is_own and request_type in ("station", "district", "scenario"):
            if "현장 확인" not in draft:
                issues.append("현장 확인 항목이 없음")
            if "투자 결정이 아니라" not in draft:
                issues.append("투자 결정이 아니라는 안내가 없음")
        result = {"passed": not issues, "issues": issues}
        return result, self._usage(draft, str(result))


class QualityLLM(FakeLLM):
    """최종 답변 작성 전담 (고품질·고비용)."""

    # ── 에너지 도메인 ──────────────────────────────────────────────

    @traceable(run_type="llm", name="quality_llm.judge_energy")
    def judge_energy(self, request_type: str, facts: list[dict]) -> tuple[dict, dict]:
        """분석 에이전트 판별(fake) — 사실 [F]만 보고 병목·유휴 위험·급속 후보를 판별하는 함수

        의도적 오판 1건: 경유는 감소율 순위 사실을 보지 않고 "줄었으면 유휴 위험"으로 판별한다.
        동대문구(경유 -4.8%, 25구 중 24위)에서 권위 게이트가 이를 교정하는 장면이 매번 재현된다.

        Args:
            request_type: "station" | "district" | "scenario"
            facts: 번호가 붙은 사실 목록
        Returns:
            (항목별 {"value", "facts", "reason"} 판별 dict, 사용량)
        """
        by_key = {f["key"]: f for f in facts}

        def value(key):
            return by_key[key]["value"] if key in by_key else None

        def ids(*keys):
            return [by_key[k]["id"] for k in keys if k in by_key]

        if request_type == "watch":
            hits = sorted((f for k, f in by_key.items() if k.startswith("new_failures:")),
                          key=lambda f: (-f["value"], f["station_id"]))
            top = [f["station_id"] for f in hits[:3]]
            result = {
                "alert_needed": {"value": bool(hits), "facts": ids("stations_affected"),
                                 "reason": "주변에 새로 멈춘 급속이 있는 자사 거점이 있음"},
                "alert_stations": {"value": top, "facts": [f["id"] for f in hits[:3]],
                                   "reason": "새 고장이 많은 순", "reasons": {}},
            }
            return result, self._usage(str(facts), str(result))

        if request_type == "scenario":
            result = {}
            for when, word in (("before", "추가 전"), ("after", "추가 후")):
                rank = value(f"score_rank_{when}")
                inside = rank is not None and rank <= 5
                result[f"ev_bottleneck_{when}"] = {
                    "value": inside if rank is not None else None,
                    "facts": ids(f"score_rank_{when}"),
                    "reason": f"{word} 병목 점수 순위가 상위 5위 {'안' if inside else '밖'}"}
            return result, self._usage(str(facts), str(result))

        growth, seoul_growth = value("ev_growth_pct"), value("seoul_ev_growth_pct")
        density, seoul_density = value("fast_per_10k_ev"), value("seoul_fast_per_10k_ev")
        ev_known = None not in (growth, seoul_growth, density, seoul_density)
        ev = ev_known and growth >= seoul_growth and density <= seoul_density
        diesel = value("diesel_rate_pct")
        lpg_rank, n = value("lpg_decline_rank"), by_key.get("lpg_decline_rank", {}).get("gu_count", 25)

        result = {
            "ev_bottleneck": {
                "value": ev if ev_known else None,
                "facts": ids("ev_growth_pct", "seoul_ev_growth_pct",
                             "fast_per_10k_ev", "seoul_fast_per_10k_ev"),
                "reason": "전기차 증가가 서울보다 빠르고 급속 밀도는 서울보다 낮음"
                          if ev else "증가 속도나 급속 밀도가 서울 평균보다 나쁘지 않음"},
            "diesel_idle": {
                "value": diesel < 0 if diesel is not None else None,
                "facts": ids("diesel_rate_pct"),
                "reason": "경유 차량이 1년 사이 줄었음"},
            "lpg_idle": {
                "value": lpg_rank <= int(n * 0.2) if lpg_rank is not None else None,
                "facts": ids("lpg_rate_pct", "lpg_decline_rank"),
                "reason": "LPG 감소율이 25구 중 많이 줄어든 쪽 20% 안"
                          if lpg_rank is not None and lpg_rank <= int(n * 0.2)
                          else "LPG 감소율이 많이 줄어든 쪽 20% 밖"},
        }
        result["fast_candidates"] = self._fast_candidates(by_key, bool(ev))
        return result, self._usage(str(facts), str(result))

    @staticmethod
    def _fast_candidates(by_key: dict, ev_bottleneck: bool) -> dict:
        if not ev_bottleneck or "cluster_median_fast_1km" not in by_key:
            return {"value": [], "facts": [], "reason": "전기 병목 자치구가 아님", "reasons": {}}
        median = by_key["cluster_median_fast_1km"]["value"]
        gas = [f for k, f in by_key.items() if k.startswith("fast_1km:") and f["prodcd"] == "B027"]
        eligible = [f for f in gas
                    if by_key[f"in_station_fast:{f['station_id']}"]["value"] == 0
                    and f["value"] <= median]
        chosen = sorted(eligible, key=lambda f: (f["value"], f["station_id"]))[:2]
        reasons = {f["station_id"]: {
            "facts": [f["id"], by_key[f"in_station_fast:{f['station_id']}"]["id"],
                      by_key["cluster_median_fast_1km"]["id"]],
            "text": "주유소 안 급속이 없고 반경 1km 급속이 묶음 중앙값 이하"} for f in chosen}
        return {"value": [f["station_id"] for f in chosen],
                "facts": [by_key["cluster_median_fast_1km"]["id"]],
                "reason": "반경 1km 급속이 적은 순으로 최대 2곳", "reasons": reasons}

    @traceable(run_type="llm", name="quality_llm.write_energy")
    async def write_energy(self, request_type: str, facts: list[dict], verdicts: dict,
                           corrections: list[dict], is_own: bool, errors: list[dict],
                           site_checks: tuple, item_labels: dict, replanned_from=None,
                           feedback=None, on_token=None) -> tuple[str, dict]:
        """작성 에이전트(fake) — 사실 [F]와 확정 판정 [J]로 답변을 쓰는 함수

        Args:
            request_type: "station" | "district" | "scenario"
            facts: 번호가 붙은 사실 목록
            verdicts: 게이트 확정 판정
            corrections: 게이트가 교정한 항목
            is_own: 자사 거점 여부 (타사면 공개 정보만)
            errors: 조회에 실패한 Tool 목록
            site_checks: 현장 확인 항목
            item_labels: 판정 항목 키 → 화면 이름
            replanned_from: 재계획 전 자치구 (재계획했을 때만)
            feedback: 검증 에이전트의 지적 (재작성 때)
            on_token: 토큰 스트리밍 콜백
        Returns:
            (답변 문자열, 사용량)
        """
        lines = EnergyAnswer(facts, verdicts).build(
            request_type, corrections, is_own, errors, site_checks, item_labels, replanned_from)
        draft = "\n".join(lines)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(str(facts) + str(verdicts) + str(feedback), draft)
