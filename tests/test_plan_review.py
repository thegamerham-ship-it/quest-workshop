"""기획 검수자: 설정 검수와 동시에, 서로 안 보고 검수. 기획 의견은 재작성을 강제하지 않는다."""
from quest_workshop.graph import build_graph
from quest_workshop.llm import FakeLLM
from quest_workshop.lore import load_cards
from quest_workshop.run import DEMO, run_one

CARDS = load_cards()


class SpyLLM(FakeLLM):
    """기획 검수자가 무엇을 받았는지, 재작성 때 어떤 의견이 넘어갔는지 기록한다."""

    def __init__(self):
        self.plan_inputs, self.feedbacks = [], []

    def plan_review(self, draft, request, cards, chapter, guide=None):
        self.plan_inputs.append({"draft": draft, "request": request, "cards": cards})
        return {"verdict": "고칠 점", "good": [], "notes": [{"point": "이장의 동기가 약하다", "fix": "왜 급한지 한 줄"}]}

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        self.feedbacks.append(list(feedback))
        return super().write(request, cards, chapter, feedback, previous, guide)


def test_both_reviewers_run_before_judge(tmp_path):
    app = build_graph(FakeLLM(), CARDS, save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    i_judge = next(i for i, l in enumerate(r["log"]) if l.startswith("[판정]"))
    assert any(l.startswith("[설정 검수]") for l in r["log"][:i_judge])
    assert any(l.startswith("[기획 검수]") for l in r["log"][:i_judge])
    assert "verdict" in r["plan_review"]


def test_plan_notes_do_not_force_rewrite(tmp_path):
    llm = SpyLLM()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    assert r["stop_reason"] == "approved"
    assert r["rewrite_count"] == 0
    assert any("기획자가 승인 때 판단" in l for l in r["log"])


def test_plan_notes_ride_along_on_lore_rewrite(tmp_path):
    llm = SpyLLM()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    run_one(app, DEMO[1][1], [{"approve": True}], quiet=True)
    assert any("(기획 의견) 이장의 동기가 약하다" in f for f in llm.feedbacks[1])


def test_plan_reviewer_does_not_see_lore_review_or_spoilers(tmp_path):
    llm = SpyLLM()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    got = llm.plan_inputs[0]
    assert set(got) == {"draft", "request", "cards"}
    assert all("spoiler" not in c for c in got["cards"])


def test_draft_has_intent_and_missing_intent_is_flagged():
    llm = FakeLLM()
    draft = llm.write(DEMO[0][1], [c.for_writer() for c in CARDS.values()], 1, [], None)
    assert draft["intent"].strip()
    pr_ok = llm.plan_review(draft, DEMO[0][1], [], 1)
    assert not any("의도" in n["point"] for n in pr_ok["notes"])
    pr = llm.plan_review({**draft, "intent": ""}, DEMO[0][1], [], 1)
    assert pr["verdict"] == "고칠 점"
    assert any("의도" in n["point"] for n in pr["notes"])


def test_plan_prompt_has_extended_checks():
    from quest_workshop.llm import PLAN_REVIEWER_PROMPT
    for key in ("intent", "단계를 자르라", "행동과 주제", "가드레일", "rule_world_01", "일일·서브 퀘스트"):
        assert key in PLAN_REVIEWER_PROMPT
