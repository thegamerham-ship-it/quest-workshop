"""메인 시나리오 사건은 서브 퀘스트에서 쓰지 않는다 + 기획자 의견은 지침으로 남아 다음 퀘스트에 반영된다."""
from quest_workshop.graph import build_graph
from quest_workshop.guide import add_rule, load_rules, remove_rule
from quest_workshop.llm import FakeLLM, guide_block
from quest_workshop.lore import load_cards, load_main_story
from quest_workshop.propose import answer, build_batch_graph
from quest_workshop.run import DEMO, run_one

CARDS = load_cards()
BOSS = "말이 통한 기사와 힘을 합쳐 뒷산 바위 제단의 두억시니를 토벌하는 퀘스트"


class Spy(FakeLLM):
    def __init__(self, extra=None):
        self.extra, self.guides = extra or [], {"write": [], "plan": [], "propose": []}

    def propose(self, cards, chapter, existing, n, guide=None):
        self.guides["propose"].append(guide)
        res = super().propose(cards, chapter, existing, n, guide)
        res["items"] = self.extra + res["items"]
        return res

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        self.guides["write"].append(guide)
        return super().write(request, cards, chapter, feedback, previous, guide)

    def plan_review(self, draft, request, cards, chapter, guide=None):
        self.guides["plan"].append(guide)
        return super().plan_review(draft, request, cards, chapter, guide)


def test_main_story_file_has_confirmed_beats():
    beats = {b["beat_id"]: b for b in load_main_story(1)}
    assert beats["main_ch1_boss"]["confirmed"] and "두억시니" in beats["main_ch1_boss"]["block_terms"]
    assert beats["main_ch1_earwine"]["confirmed"] and "귀밝이술" in beats["main_ch1_earwine"]["block_terms"]


def test_main_story_proposal_is_filtered(tmp_path):
    llm = Spy(extra=[{"request": BOSS, "why": "1장 클라이맥스"}])
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    out = build_batch_graph(llm, app, CARDS, save_dir=tmp_path).invoke({"chapter": 1, "n": 3})
    assert all("두억시니" not in r["request"] for r in out["results"])
    assert any("걸러 냄(메인 퀘스트 몫" in line for line in out["log"])
    assert len(out["results"]) == 3
    assert llm.guides["propose"][0]["main_beats"]          # 제안자도 메인 사건 목록을 받는다


def test_direct_main_story_request_stops_for_review(tmp_path):
    app = build_graph(FakeLLM(), CARDS, save_dir=tmp_path)
    r = run_one(app, BOSS, [], quiet=True)
    assert r["stop_reason"] == "needs_review", r["log"]
    assert any("메인 시나리오 사건" in line for line in r["log"])


def test_planner_feedback_is_remembered_for_later_quests(tmp_path):
    llm = Spy()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    fb = "기사와 말이 통하는 장면은 메인 몫이라 쓰지 않는다"
    r = run_one(app, DEMO[0][1], [{"approve": False, "feedback": fb}, {"approve": True}], quiet=True)
    assert r["stop_reason"] == "approved"
    assert [x["text"] for x in load_rules(tmp_path)] == [fb]
    # 같은 퀘스트를 다시 쓸 때 기획 검수자는 기획자 의견을 받아 '요청대로 되돌려라'고 하지 않는다
    assert llm.guides["plan"][-1]["human_feedback"] == fb
    # 다른 퀘스트·다음 제안에도 지침으로 들어간다
    run_one(app, DEMO[1][1], [{"approve": True}], quiet=True)
    assert fb in llm.guides["write"][-1]["rules"]
    build_batch_graph(llm, app, CARDS, save_dir=tmp_path).invoke({"chapter": 1, "n": 1})
    assert fb in llm.guides["propose"][-1]["rules"]


def test_rules_can_be_removed_and_are_not_duplicated(tmp_path):
    add_rule(tmp_path, "보상은 묻지 않는다")
    add_rule(tmp_path, "보상은 묻지 않는다")
    (rule,) = load_rules(tmp_path)
    remove_rule(tmp_path, rule["id"])
    assert load_rules(tmp_path) == []


def test_guide_block_puts_planner_over_request():
    text = guide_block({"main_beats": [{"title": "귀밝이술로 말이 통함", "summary": "…"}],
                        "rules": ["메인 몫은 쓰지 않는다"], "human_feedback": "다른 사건으로"})
    assert "메인 시나리오 사건" in text and "요청 문장보다 우선" in text and "가장 우선" in text
    assert guide_block(None) == "" and guide_block({"main_beats": [], "rules": []}) == ""


def test_all_main_beats_confirmed():
    assert all(b["confirmed"] for b in load_main_story(1))


def test_unlock_condition_checked_and_shown():
    from quest_workshop.checks import unlock_check
    from quest_workshop.lore import unlock_text
    beats = load_main_story(1)
    ok = {"unlock": {"after": "main_ch1_ritual", "before": "main_ch1_earwine"}}
    assert unlock_check(ok, beats) == []
    assert "어긋난 액막이제 이후" in unlock_text(ok["unlock"]) and "귀밝이술로 말이 통함 전" in unlock_text(ok["unlock"])
    backwards = {"unlock": {"after": "main_ch1_earwine", "before": "main_ch1_ritual"}}
    unknown = {"unlock": {"after": "main_ch9_x", "before": ""}}
    assert unlock_check(backwards, beats) and unlock_check(unknown, beats)


def test_fake_draft_has_unlock(tmp_path):
    app = build_graph(FakeLLM(), CARDS, save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    assert r["draft"]["unlock"]["after"] == "main_ch1_ritual"


def test_discarded_request_is_not_proposed_again(tmp_path):
    from quest_workshop.guide import add_discard
    llm = Spy()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    batch = build_batch_graph(llm, app, CARDS, save_dir=tmp_path)
    first = batch.invoke({"chapter": 1, "n": 1})["results"][0]["request"]
    add_discard(tmp_path, first, "메인 흐름과 겹침")
    second = batch.invoke({"chapter": 1, "n": 1})["results"][0]["request"]
    assert second != first
    assert any("버린 이유: 메인 흐름과 겹침" in d for d in llm.guides["propose"][-1]["discarded"])
