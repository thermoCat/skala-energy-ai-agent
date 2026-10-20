"""Ollama·OpenAI 어댑터에 붙이는 에너지용 LLM 메서드 (fake_llm 과 같은 인터페이스)

어댑터는 _json_call(JSON 한 번 받기)과 _text_stream(답변 문장 받기)만 구현하면 된다.
"""
from langsmith import traceable

from energy.seoul import BRAND_ALIASES

from . import energy_prompts as p


class EnergyFastMixin:
    """fast 모델 역할: Supervisor 분류, 검증 에이전트 LLM-as-Judge"""

    @traceable(run_type="llm", name="energy.classify")
    def classify_energy(self, query: str, gu_names: tuple, brand_names: tuple) -> tuple[dict, dict]:
        """질문에서 자치구·주유소 이름·회사·급속 N기를 뽑는 함수

        Args:
            query: 사용자 질문
            gu_names: 서울 자치구 이름 목록
            brand_names: 회사 표현 목록
        Returns:
            ({"gu", "station_name", "brand", "fast_add", "kw"}, 사용량)
        """
        content, usage = self._json_call(p.CLASSIFY_SYSTEM,
                                         p.classify_user(query, gu_names, brand_names),
                                         p.CLASSIFY_SCHEMA)
        return p.normalize_classify(content, query, gu_names, BRAND_ALIASES), usage

    @traceable(run_type="llm", name="energy.judge_answer")
    def judge_answer(self, draft: str, request_type: str, is_own: bool) -> tuple[dict, dict]:
        """답변이 근거 표기·현장 확인·투자 결정 아님 기준을 지켰는지 보는 함수

        Args:
            draft: 답변 초안
            request_type: 요청 종류
            is_own: 자사 거점 여부
        Returns:
            ({"passed", "issues"}, 사용량)
        """
        content, usage = self._json_call(p.ANSWER_JUDGE_SYSTEM,
                                         p.answer_judge_user(draft, request_type, is_own),
                                         p.ANSWER_JUDGE_SCHEMA)
        return p.normalize_answer_judgement(content), usage


class EnergyQualityMixin:
    """quality 모델 역할: 분석 에이전트 판별, 작성 에이전트"""

    @traceable(run_type="llm", name="energy.judge")
    def judge_energy(self, request_type: str, facts: list[dict]) -> tuple[dict, dict]:
        """사실 [F]만 보고 병목·유휴 위험·급속 후보(또는 시나리오 병목 전후)를 판별하는 함수

        Args:
            request_type: "station" | "district" | "scenario"
            facts: 번호 붙은 사실 목록
        Returns:
            (항목별 판별 dict, 사용량)
        """
        system, user, schema = p.judge_prompt(request_type, facts)
        content, usage = self._json_call(system, user, schema)
        return p.normalize_judgement(content, request_type, facts), usage

    @traceable(run_type="llm", name="energy.write")
    async def write_energy(self, request_type: str, facts: list[dict], verdicts: dict,
                           corrections: list[dict], is_own: bool, errors: list[dict],
                           site_checks: tuple, item_labels: dict, replanned_from=None,
                           feedback=None, on_token=None) -> tuple[str, dict]:
        """사실과 확정 판정으로 답변을 쓰는 함수 (토큰 스트리밍)

        Args:
            request_type: 요청 종류
            facts: 번호 붙은 사실 목록
            verdicts: 확정 판정
            corrections: 게이트 교정 항목
            is_own: 자사 거점 여부
            errors: 조회 실패 Tool
            site_checks: 현장 확인 항목
            item_labels: 판정 항목 이름
            replanned_from: 재계획 전 자치구
            feedback: 검증 반려 사유
            on_token: 토큰 콜백
        Returns:
            (답변, 사용량)
        """
        system, user = p.write_prompt(request_type, facts, verdicts, corrections, is_own,
                                      errors, site_checks, item_labels, replanned_from, feedback)
        return await self._text_stream(system, user, on_token)
