"""반복 퀘스트 변형: 골격(단계·보상·열림 조건)은 그대로, 의뢰인·대사만 바뀐다."""
from quest_workshop.graph import build_graph
from quest_workshop.llm import FakeLLM
from quest_workshop.lore import load_cards
from quest_workshop.run import DEMO, run_one
from quest_workshop.variants import giver_candidates, make_variants, skeleton_issues, to_markdown


def approved(tmp_path):
    app = build_graph(FakeLLM(), load_cards(), save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    return {"request": DEMO[0][1], "chapter": 1, "draft": r["draft"]}


def test_three_variants_keep_skeleton_and_change_giver(tmp_path):
    rec = approved(tmp_path)
    res = make_variants(FakeLLM(), rec, on_log=lambda *_: None)
    vs = res["variants"]
    assert len(vs) == 3
    assert len({v["draft"]["giver"] for v in vs} | {rec["draft"]["giver"]}) == 4   # 의뢰인 모두 다름
    for v in vs:
        d = v["draft"]
        assert d["reward"] == rec["draft"]["reward"] and d["unlock"] == rec["draft"]["unlock"]
        assert len(d["steps"]) == len(rec["draft"]["steps"])
        assert not v["skeleton_issues"] and v["lore_ok"]
        assert not v["copied_lines"]
    assert "사람 판정" in to_markdown(res)


def test_candidates_exclude_protagonist_and_base_giver():
    cards = load_cards()
    ids = {c.card_id for c in giver_candidates(cards, 1, exclude="char_elder_01")}
    assert "char_dosa_01" not in ids and "char_elder_01" not in ids and "char_machine_01" not in ids
    assert "char_jumo_01" in ids


def test_skeleton_change_is_flagged():
    base = {"steps": [{"order": 1, "text": "a", "lore_refs": ["char_elder_01"]},
                      {"order": 2, "text": "b", "lore_refs": ["place_mountain_01"]}]}
    var = {"steps": [{"order": 1, "text": "a", "lore_refs": ["char_jumo_01"]},
                     {"order": 2, "text": "b", "lore_refs": ["place_tavern_01"]}]}
    assert skeleton_issues(base, var, "char_elder_01", "char_jumo_01")
    assert not skeleton_issues(base, {"steps": base["steps"]}, "char_elder_01", "char_elder_01")
