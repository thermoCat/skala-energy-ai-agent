"""LLM_BACKEND=openai — OpenAI API 백엔드 (fake_llm 과 같은 인터페이스) ⚠️ 실제 과금

fast(기본 gpt-4o-mini)는 분류·답변 판정·일반 대화, quality(기본 gpt-4o)는 판별·작성을 맡는다.
JSON 모드는 형식만 보장하고 스키마는 강제하지 않아, 출력은 energy_prompts 가 코드로 다듬는다.
cost_usd 는 응답의 total_tokens × 단일 가상 단가다 (입력·출력 단가 차이는 단순화).

사전 조건: 유효한 `OPENAI_API_KEY` (.env).
"""
import os

import httpx
from langsmith import traceable

from .energy_llm import EnergyFastMixin, EnergyQualityMixin

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        resp = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    # 에너지 메서드용 — JSON 모드는 스키마를 강제하지 않아 형식은 energy_prompts 가 다듬는다
    def _json_call(self, system: str, user: str, schema: dict,
                   think: bool = False) -> tuple[str, dict]:
        return self._chat(system, user, json_mode=True)

    async def _text_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        content, usage = self._chat(system, user)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage


class OpenAIFastLLM(EnergyFastMixin, OpenAILLM):
    """fast 모델 역할: Supervisor 분류, 정책·경쟁 판별, 답변 판정, 일반 대화 (기본 gpt-4o-mini)"""

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        # 스트리밍 호출을 쓰지 않아 완성된 답을 on_token 으로 한 번에 보낸다
        system = ("당신은 간결한 한국어 비서다. 한두 문장으로 답하라. "
                  "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라.")
        rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
        user = (f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n"
                f"## 사용자의 새 메시지\n{query}")
        content, usage = self._chat(system, user)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage


class OpenAIQualityLLM(EnergyQualityMixin, OpenAILLM):
    """quality 모델 역할: 분석 에이전트 판별, 작성 에이전트 (기본 gpt-4o)"""
