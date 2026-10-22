"""권위 게이트 교정 표시 테스트 — 확정 판정마다 교정 여부·AI 원래 판별이 남는지"""
from agent.fake_llm import QualityLLM
from tests.conftest import event_types


def test_corrected_item_is_tagged_and_drops_ai_reason(ask, store):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")
    diesel, ev = result["verdicts"]["diesel_idle"], result["verdicts"]["ev_bottleneck"]

    assert diesel["corrected"] is True and diesel["llm_value"] is True and diesel["value"] is False
    assert diesel["reason"].startswith("기준에 따라 확정")       # 틀린 판별의 이유는 남기지 않음
    assert ev["corrected"] is False and ev["reason"] == result["llm_verdicts"]["ev_bottleneck"]["reason"]
    assert f"[{diesel['id']}](교정됨)" in result["final"]
    assert f"[{ev['id']}](교정됨)" not in result["final"]

    gate = [e for e in result["events"] if e["type"] == "Judged" and e["by"] == "gate"][0]
    assert gate["corrected_items"] == ["diesel_idle"]
    logged = store.load_decisions("A0009172")[0]
    assert '"corrected": true' in logged["verdict"]                # 판단 이력에도 표시


class _OneCandidateOnly(QualityLLM):
    """충전기 후보를 1곳만 고르는 분석 에이전트 (게이트가 나머지를 더하는지 확인용)"""

    def judge_energy(self, request_type, facts):
        result, usage = super().judge_energy(request_type, facts)
        if "fast_candidates" in result:
            fast = result["fast_candidates"]
            fast["value"] = fast["value"][:1]
            fast["reasons"] = {k: v for k, v in fast["reasons"].items() if k in fast["value"]}
        return result, usage


def test_candidate_added_by_gate_is_marked(ask):
    result = ask("동대문구 SK 거점 8곳을 어떻게 나눠 써야 해?", gu="동대문구",
                 quality=_OneCandidateOnly("quality", 0.0))
    fast = result["verdicts"]["fast_candidates"]
    assert fast["value"] == ["A0009172", "A0009248"] and fast["corrected"] is True
    assert fast["picked_by_llm"] == ["A0009172"]
    assert set(fast["reasons"]) == {"A0009172"}                    # 게이트가 더한 곳엔 AI 이유 없음
    cards = {e["station_id"]: e["picked_by"] for e in result["events"] if e["type"] == "EvidenceCard"}
    assert cards == {"A0009172": "llm", "A0009248": "gate"}
    assert result["validation"]["passed"] is True
