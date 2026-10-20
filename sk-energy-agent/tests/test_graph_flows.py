"""Supervisor 그래프 흐름 테스트 (묶음·시나리오·이름 찾기·재계획·장애 격리·검증 재작성)"""
from agent.fake_llm import QualityLLM
from agent.validation import check_answer
from tests.conftest import event_types


def test_district_cluster_flow(ask, store):
    result = ask("동대문구 SK 거점 8곳을 어떻게 나눠 써야 해?", gu="동대문구")

    assert result["request_type"] == "district"
    assert result["verdicts"]["fast_candidates"]["value"] == ["A0009172", "A0009248"]
    assert [c["item"] for c in result["gate_corrections"]] == ["diesel_idle"]
    cards = [e for e in result["events"] if e["type"] == "EvidenceCard"]
    assert [c["station_id"] for c in cards] == ["A0009172", "A0009248"]
    assert "풍한주유소" in result["final"] and "삼영주유소" in result["final"]
    assert store.load_decisions("동대문구")[0]["scope_type"] == "gu"


def test_district_named_in_text_without_map_click(ask):
    result = ask("동대문구 SK 거점 어떻게 나눠 써야 해?")
    assert result["request_type"] == "district" and result["gu"] == "동대문구"


def test_scenario_still_bottleneck(ask):
    result = ask("급속 2기 넣으면?", station_id="A0009172")
    assert result["request_type"] == "scenario"
    assert result["gate_corrections"] == []
    assert "여전히 전기 병목입니다" in result["final"] and "3.9대" in result["final"]


def test_scenario_leaves_bottleneck(ask):
    result = ask("급속 3기 넣으면?", station_id="A0000379")    # 동작구, 점수 16 경계
    assert "전기 병목에서 벗어납니다" in result["final"]


def test_scenario_never_bottleneck(ask):
    result = ask("박미주유소에 급속 2기 넣으면?")              # 이름 → 금천구 박미주유소
    assert result["station_id"] == "A0001146" and result["request_type"] == "scenario"
    assert "추가 전후 모두 전기 병목이 아닙니다" in result["final"]


def test_same_name_asks_back(ask):
    result = ask("신성주유소 어때?")
    assert event_types(result) == ["RouteDecided", "StationResolved", "AskUser"]
    assert "2곳" in result["final"] and "구로구" in result["final"] and "노원구" in result["final"]


def test_same_name_with_company_resolves_to_other_company(ask):
    result = ask("S-OIL 신성주유소 어때?")
    assert result["station_id"] == "A0010117" and result["is_own"] is False
    assert "투자 판별은 하지 않습니다" in result["final"]


def test_replan_to_nearest_district_with_own_stations(ask):
    # GS칼텍스는 동작구에 거점이 없음 → 가장 가까운 관악구로 다시 계획
    result = ask("동작구 거점 어떻게 써야 해?", company="GS칼텍스")
    assert result["replanned_from"] == "동작구" and result["gu"] == "관악구"
    assert "Replanned" in event_types(result)
    assert "동작구에는 자사 거점이 없어" in result["final"]


class _FailingTool:
    """지정한 Tool만 연결 실패를 내고 나머지는 실제 MCP 서버로 보내는 감싸개"""

    def __init__(self, client, name):
        self.client, self.name = client, name

    async def call_tool(self, name, arguments, server="ops"):
        if name == self.name:
            raise ConnectionError("수소 API 연결 실패 (테스트)")
        return await self.client.call_tool(name, arguments, server)


def test_one_tool_failure_is_isolated(ask):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172",
                 wrap_mcp=lambda mcp: _FailingTool(mcp, "get_h2_status"))
    assert [e["tool"] for e in result["errors"]] == ["get_h2_status"]
    assert "수소: 데이터 없음" in result["final"]
    assert result["verdicts"]["ev_bottleneck"]["value"] is True    # 나머지 판정은 그대로
    assert result["validation"]["passed"] is True


class _SloppyFirstDraft(QualityLLM):
    """첫 초안에만 숫자 하나를 틀리게 쓰는 작성 에이전트 (검증 재작성 확인용)"""

    async def write_energy(self, *args, **kwargs):
        draft, usage = await super().write_energy(*args, **kwargs)
        if not kwargs.get("feedback"):
            draft = draft.replace("317.1", "320.0", 1)
        return draft, usage


def test_validator_rejects_wrong_number_and_rewrites(ask):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172",
                 quality=_SloppyFirstDraft("quality", 0.0))
    checks = [e for e in result["events"] if e["type"] == "Validation"]
    assert checks[0]["verdict"] == "불합격 → 재작성"
    assert "근거 없는 숫자: 320.0" in checks[0]["issues"]
    assert checks[1]["verdict"] == "합격"
    assert "317.1" in result["final"] and "320.0" not in result["final"]


def test_check_answer_unit():
    facts = [{"id": "F1", "key": "x", "label": "동대문구 전기차 1만 대당 급속 ID",
              "value": 317.1, "unit": "ID", "source": "s"},
             {"id": "F2", "key": "fast_1km:A1", "label": "재정제2주유소 반경 1km 급속 ID",
              "value": 12, "unit": "ID", "source": "s", "name": "재정제2주유소"}]
    assert check_answer("1) 밀도 317.1[F1], 재정제2주유소 반경 1km 12[F2]", facts, {}) == []
    issues = check_answer("밀도 320.0[F1] [F9]", facts, {})
    assert "근거 없는 숫자: 320.0" in issues and "없는 인용 번호: F9" in issues


def test_plain_chat_still_goes_to_chat(ask):
    assert ask("안녕하세요")["route"] == "chat"
