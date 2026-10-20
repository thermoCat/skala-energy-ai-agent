"""급속 후보 "어디부터, 왜" 테스트 — 기준 없는 판단은 AI가, 코드는 검사만"""
from agent.fake_llm import QualityLLM
from agent.validation import check_priority
from tests.conftest import event_types

PUNGHAN, SAMYOUNG = "A0009172", "A0009248"


def _prioritized(result):
    return [e for e in result["events"] if e["type"] == "Prioritized"]


def test_ai_orders_candidates_with_cited_reasons(ask):
    result = ask("동대문구 SK 거점 어떻게 나눠 써야 해?", gu="동대문구")
    priority = result["priority"]
    assert priority["by"] == "llm" and priority["order"] == [PUNGHAN, SAMYOUNG]
    by_id = {f["id"]: f for f in result["facts"]}
    for sid, reason in priority["reasons"].items():          # 이유는 그 거점의 사실을 인용
        assert any(by_id[x]["station_id"] == sid for x in reason["facts"])
    assert "먼저 볼 순서 (AI 판단)" in result["final"]
    assert result["verdicts"]["fast_candidates"]["value"] == [PUNGHAN, SAMYOUNG]  # 확정 판정은 게이트 것 그대로
    assert result["validation"]["passed"] is True


def test_held_decision_moves_candidate_back(ask, store):
    store.record_human_decision("SK에너지", PUNGHAN, "동대문구", "보류", None, "부지 협의", "담당자")
    result = ask("동대문구 SK 거점 어떻게 나눠 써야 해?", gu="동대문구")
    assert result["priority"]["order"] == [SAMYOUNG, PUNGHAN]  # 사람의 보류 결정을 저울질에 반영
    assert "지난 결정 있음" in result["priority"]["reasons"][PUNGHAN]["text"]


def test_station_answer_says_its_place(ask):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id=SAMYOUNG)
    assert "묶음 후보 2곳 중 두 번째로 볼 곳 (AI 판단:" in result["final"]


def test_single_candidate_skips_priority(ask):
    result = ask("동작구 SK 거점 어떻게 써야 해?", gu="동작구")      # 후보 1곳 (살피재)
    assert result["verdicts"]["fast_candidates"]["value"] == ["A0000379"]
    assert "Prioritized" not in event_types(result)


class _MadeUpReason(QualityLLM):
    """다른 거점의 사실을 인용하고 없는 숫자를 지어내는 우선순위 판단 (검사 확인용)"""

    def prioritize(self, facts, candidates):
        result, usage = super().prioritize(facts, candidates)
        other = next(f["id"] for f in facts if f["station_id"] == candidates[1])
        result["reasons"][candidates[0]] = {"text": "주변 급속 99개[F1]가 남아 여유", "facts": [other]}
        return result, usage


def test_bad_priority_falls_back_and_says_so(ask):
    result = ask("동대문구 SK 거점 어떻게 나눠 써야 해?", gu="동대문구",
                 quality=_MadeUpReason("quality", 0.0))
    [event] = _prioritized(result)
    assert event["by"] == "fallback" and result["priority"]["order"] == [PUNGHAN, SAMYOUNG]
    assert any("근거 없는 숫자" in x for x in event["issues"])
    assert any("인용 없음" in x for x in event["issues"])
    assert "AI 우선순위 판단이 검사를 통과하지 못해" in result["final"]
    assert "99" not in result["final"] and result["validation"]["passed"] is True


def test_check_priority_unit():
    facts = [{"id": "F1", "key": "fast_1km:A", "label": "A 반경", "value": 10, "unit": "ID",
              "source": "s", "station_id": "A", "name": "A"},
             {"id": "F2", "key": "fast_1km:B", "label": "B 반경", "value": 16, "unit": "ID",
              "source": "s", "station_id": "B", "name": "B"}]
    good = {"order": ["B", "A"], "reasons": {"A": {"text": "급속 10개[F1]", "facts": ["F1"]},
                                            "B": {"text": "급속 16개[F2]", "facts": ["F2"]}}}
    assert check_priority(good, ["A", "B"], facts) == []
    assert check_priority({**good, "order": ["A"]}, ["A", "B"], facts) == ["순서가 확정 후보와 다름"]
