"""제안 에이전트: 요청 N개 제안 → 각각 작성·검수 → 승인 대기. API 없이 가짜 모델로 돈다."""
from quest_workshop.graph import build_graph
from quest_workshop.llm import FakeLLM
from quest_workshop.lore import load_cards
from quest_workshop.propose import answer, build_batch_graph, load_existing

CARDS = load_cards()


def make(tmp_path, llm=None):
    llm = llm or FakeLLM()
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    return app, build_batch_graph(llm, app, CARDS, save_dir=tmp_path)


def test_propose_five_all_waiting_for_approval(tmp_path):
    app, batch = make(tmp_path)
    out = batch.invoke({"chapter": 1, "n": 5})
    assert len(out["results"]) == 5
    assert len({r["request"] for r in out["results"]}) == 5
    assert all(r["status"] == "승인 대기" for r in out["results"]), [r["log"] for r in out["results"]]


def test_approved_quests_are_not_proposed_again(tmp_path):
    app, batch = make(tmp_path)
    first = batch.invoke({"chapter": 1, "n": 3})
    for r in first["results"]:
        assert answer(app, r["thread_id"], {"approve": True})["stop_reason"] == "approved"
    assert len(load_existing(tmp_path)) == 3
    second = batch.invoke({"chapter": 1, "n": 3})
    old = {r["request"] for r in first["results"]}
    assert old.isdisjoint(r["request"] for r in second["results"])


class LaterChapterProposer(FakeLLM):
    def propose(self, cards, chapter, existing, n, guide=None):
        res = super().propose(cards, chapter, existing, n, guide)
        res["items"] = [{"request": "도사가 기계 동료와 함께 기사를 구하는 퀘스트", "why": ""}] + res["items"]
        return res


def test_later_chapter_proposal_is_filtered(tmp_path):
    app, batch = make(tmp_path, LaterChapterProposer())
    out = batch.invoke({"chapter": 1, "n": 3})
    assert all("기계" not in r["request"] for r in out["results"])
    assert any("걸러 냄(뒷장 요소)" in line for line in out["log"])
    assert len(out["results"]) == 3


def test_runs_out_of_material_reports_needed_lore(tmp_path):
    app, batch = make(tmp_path)
    for r in batch.invoke({"chapter": 1, "n": 5})["results"]:
        answer(app, r["thread_id"], {"approve": True})
    out = batch.invoke({"chapter": 1, "n": 5})
    assert len(out["results"]) == 2          # 남은 소재 2개만, 억지로 5개를 채우지 않음
    assert out["needed_lore"]
    assert any("새 소재 부족" in line for line in out["log"])
    assert any("많이 쓴 카드" in line for line in out["log"])


class RepeatingProposer(FakeLLM):
    """이미 승인된 요청을 조금만 바꿔 다시 내는 제안자."""

    def propose(self, cards, chapter, existing, n, guide=None):
        if existing:
            return {"items": [{"request": "장터에 몰려든 담비굴 소인들을 쫓아 달라는 이장의 퀘스트", "why": ""}],
                    "needed_lore": []}
        return super().propose(cards, chapter, existing, n, guide)


def test_near_copy_of_existing_is_filtered(tmp_path):
    app, batch = make(tmp_path, RepeatingProposer())
    for r in batch.invoke({"chapter": 1, "n": 2})["results"]:
        answer(app, r["thread_id"], {"approve": True})
    out = batch.invoke({"chapter": 1, "n": 1})
    assert out.get("results", []) == []
    assert any("거의 같음" in line for line in out["log"])


def test_similarity_catches_copy_not_different_quests():
    from quest_workshop.llm import FAKE_PROPOSALS
    from quest_workshop.propose import SIMILAR, similarity
    reqs = [r for r, _ in FAKE_PROPOSALS]
    assert all(similarity(a, b) < SIMILAR for i, a in enumerate(reqs) for b in reqs[i + 1:])
    assert similarity(reqs[1], "장터에 몰려든 담비굴 소인들을 쫓아 달라는 이장의 퀘스트") >= SIMILAR
