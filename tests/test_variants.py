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


def test_failed_variant_is_reported_not_hidden(tmp_path):
    rec = approved(tmp_path)

    class Broken(FakeLLM):
        def variant(self, *a, **k):
            raise RuntimeError("모델 응답 없음")

    res = make_variants(Broken(), rec, on_log=lambda *_: None)
    assert res["variants"] == []
    assert len(res["errors"]) == 3 and "모델 응답 없음" in res["errors"][0]["error"]


def one_time_quest(tmp_path):
    rec = approved(tmp_path)
    rec["draft"]["steps"].append({"order": len(rec["draft"]["steps"]) + 1,
                                  "text": "산짐승이 파헤친 자리에서 이상한 쇳조각을 발견해 챙긴다.",
                                  "lore_refs": ["place_mountain_01", "item_scrap_01"]})
    return rec


def test_one_time_event_stops_variants_until_forced(tmp_path):
    rec = one_time_quest(tmp_path)
    res = make_variants(FakeLLM(), rec, on_log=lambda *_: None)
    assert res["stopped"] and res["variants"] == []
    assert res["repeat_fit"]["one_time"][0]["step"] == len(rec["draft"]["steps"])
    assert "한 번뿐인 사건" in to_markdown(res)
    forced = make_variants(FakeLLM(), rec, on_log=lambda *_: None, force=True, fit=res["repeat_fit"])
    assert len(forced["variants"]) == 3 and not forced["stopped"]


def test_simple_quest_passes_repeat_check(tmp_path):
    res = make_variants(FakeLLM(), approved(tmp_path), on_log=lambda *_: None)
    assert res["repeat_fit"]["ok"] and not res["stopped"]


def test_giver_step_may_move_to_new_givers_place():
    base = {"steps": [{"order": 1, "text": "약초밭에서 약초꾼과 이야기한다",
                       "lore_refs": ["char_herbalist_01", "place_herbfield_01"]},
                      {"order": 2, "text": "계곡을 조사한다", "lore_refs": ["place_mountain_01"]}]}
    moved = {"steps": [{"order": 1, "text": "주막에서 주모에게 약초밭 소문을 듣는다",
                        "lore_refs": ["char_jumo_01", "place_tavern_01"]},
                       {"order": 2, "text": "계곡을 조사한다", "lore_refs": ["place_mountain_01"]}]}
    assert not skeleton_issues(base, moved, "char_herbalist_01", "char_jumo_01")
    moved["steps"][1]["lore_refs"] = ["place_tavern_01"]          # 의뢰인이 없는 단계의 장소는 그대로여야
    assert skeleton_issues(base, moved, "char_herbalist_01", "char_jumo_01")


def test_dialogue_only_flag_does_not_stop_but_guides_writer(tmp_path):
    rec = approved(tmp_path)
    seen = {}

    class Spy(FakeLLM):
        def variant(self, base, giver_card, chapter, others, guide=None):
            seen["guide"] = guide
            return super().variant(base, giver_card, chapter, others, guide)

    fit = {"ok": False, "one_time": [{"step": 0, "what": "어둑시니를 처음 보고 놀라는 대사", "why": "매번 처음 보는 듯함"}]}
    res = make_variants(Spy(), rec, on_log=lambda *_: None, fit=fit)
    assert not res["stopped"] and len(res["variants"]) == 3
    assert res["repeat_fit"]["ok"] and res["repeat_fit"]["dialogue_fixed"]
    assert "처음 보고 놀라는" in seen["guide"]["dialogue_avoid"][0]
