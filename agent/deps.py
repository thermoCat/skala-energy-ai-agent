"""의존성 주입.

노드 함수가 closure의 지역 변수에 암묵적으로 기대지 않도록, supervisor가 쓰는 모든
의존성을 SupervisorDeps 하나에 명시적으로 담아 functools.partial로 각 노드에 주입한다.
파일을 어디로 옮겨도, 단위 테스트에서 mock deps로 갈아 끼워도 동작이 같다.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class SupervisorDeps:
    fast_llm: Any        # 분류·답변 판정·정책경쟁 판별용 저비용 모델
    quality_llm: Any     # 분석 판별·답변 작성용 고품질 모델
    mcp: Any             # MCP stdio 클라이언트 (Tool/Resource/Prompt)
    session_store: Any   # 대화·판단 이력·알림 SQLite 저장소
