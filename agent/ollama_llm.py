"""LLM_BACKEND=ollama — 로컬 Ollama 실모델 백엔드 (fake_llm 과 같은 인터페이스)

fast(기본 qwen3:0.6b)는 분류·답변 판정·일반 대화, quality(기본 qwen3:8b)는 판별·작성을 맡는다.
JSON 스키마 강제(format)와 temperature=0 으로 흔들림을 줄이지만 실모델이라 결정론은 아니다 —
그래서 권위 게이트와 검증 에이전트가 실제 방어선으로 동작한다. 로컬 추론이라 과금은 없고,
cost_usd 는 가상 단가 × 실측 토큰 수다.

사전 조건: `ollama serve` 실행 중 + `ollama pull qwen3:0.6b qwen3:8b`.
"""
import json

import httpx
from langsmith import traceable

from .energy_llm import EnergyFastMixin, EnergyQualityMixin

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens

    def _chat(self, system: str, user: str, schema: dict | None = None,
              think: bool | None = None) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        if schema is not None:
            payload["format"] = schema
        if think is not None:
            payload["think"] = think
        resp = httpx.post(OLLAMA_URL, json=payload, timeout=300.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["message"]["content"]
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    async def _chat_stream(self, system: str, user: str, on_token=None,
                           think: bool | None = None) -> tuple[str, dict]:
        """사용자 대면 텍스트(최종 답변류) 전용 — Ollama NDJSON 스트림을 토큰 단위로
        받아 on_token(조각)을 즉시 호출해 SSE로 실시간 중계할 수 있게 한다. 분류/계획
        등 JSON 스키마 출력(route/plan/analyze)은 부분 출력이 의미가 없어 그대로 _chat을
        쓴다."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": True,
            "options": {"temperature": 0.0},
        }
        if think is not None:
            payload["think"] = think
        chunks: list[str] = []
        usage = {"model": self.name, "tokens": 0, "usd": 0.0}
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if data.get("done"):
                        tokens = ((data.get("prompt_eval_count") or 0)
                                  + (data.get("eval_count") or 0))
                        usage = {"model": self.name, "tokens": tokens,
                                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        return "".join(chunks), usage

    # 에너지 메서드용 — qwen3 의 생각 과정 출력은 끄고 답만 받는다 (응답 시간 단축)
    def _json_call(self, system: str, user: str, schema: dict,
                   think: bool = False) -> tuple[str, dict]:
        return self._chat(system, user, schema=schema, think=think)

    async def _text_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(system, user, on_token=on_token, think=False)


class OllamaFastLLM(EnergyFastMixin, OllamaLLM):
    """fast 모델 역할: Supervisor 분류, 정책·경쟁 판별, 답변 판정, 일반 대화 (소형 모델)"""

    @traceable(run_type="llm", name="ollama_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        system = ("당신은 간결한 한국어 비서다. 한두 문장으로 답하라. "
                  "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라.")
        rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
        user = (f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n"
                f"## 사용자의 새 메시지\n{query}")
        return await self._chat_stream(system, user, on_token=on_token)


class OllamaQualityLLM(EnergyQualityMixin, OllamaLLM):
    """quality 모델 역할: 분석 에이전트 판별, 작성 에이전트 (에너지 메서드는 EnergyQualityMixin)"""
