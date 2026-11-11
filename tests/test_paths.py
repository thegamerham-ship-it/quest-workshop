"""테스트 요청 5개가 기대한 경로(종료 사유)로 끝나는지 확인. API 없이 가짜 모델로 돈다."""
import pytest

from quest_workshop.checks import code_check
from quest_workshop.graph import build_graph
from quest_workshop.llm import FakeLLM
from quest_workshop.lore import load_cards, retrieve
from quest_workshop.run import DEMO, run_one

CARDS = load_cards()


@pytest.fixture
def app(tmp_path):
    return build_graph(FakeLLM(), CARDS, save_dir=tmp_path)


@pytest.mark.parametrize("name,request_text,answers,expected", [
    (*DEMO[0], "approved"),
    (*DEMO[1], "approved"),
    (*DEMO[2], "needs_review"),
    (*DEMO[3], "approved"),
    (*DEMO[4], "lore_gap"),
])
def test_paths(app, name, request_text, answers, expected):
    r = run_one(app, request_text, answers, quiet=True)
    assert r["stop_reason"] == expected, r["log"]


def test_conflict_rewrites_only_once(app):
    r = run_one(app, DEMO[2][1], [], quiet=True)
    assert r["rewrite_count"] == 1
    assert sum("[작성]" in line for line in r["log"]) == 2


def test_seaside_fixed_after_rewrite(app):
    r = run_one(app, DEMO[1][1], [{"approve": True}], quiet=True)
    assert "바닷가" not in r["draft"]["summary"]
    assert any("재작성 1" in line for line in r["log"])


def test_human_feedback_applied(app):
    r = run_one(app, DEMO[3][1], DEMO[3][2], quiet=True)
    elder = [d for d in r["draft"]["dialogue"] if d["speaker"] == "솔뫼 이장"][0]
    assert "어, 어이쿠" in elder["line"]


def test_reject_without_feedback_stops(app):
    r = run_one(app, DEMO[0][1], [{"approve": False}], quiet=True)
    assert r["stop_reason"] == "rejected"


def test_saved_file(app, tmp_path):
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    assert r["saved_path"].startswith(str(tmp_path))


def test_retrieve_excludes_later_chapter_cards():
    ids = [c.card_id for c in retrieve("기계 동료와 드론", CARDS, chapter=1)]
    assert "char_machine_01" not in ids


def test_code_check_catches_fake_ref_and_missing_ref():
    draft = {"title": "t", "summary": "", "steps": [
        {"order": 1, "text": "뒷산에 간다", "lore_refs": ["no_such_card"]},
        {"order": 2, "text": "돌아온다", "lore_refs": []}],
        "dialogue": [], "reward": {"text": "", "lore_refs": []}}
    kinds = {i["kind"] for i in code_check(draft, CARDS, 1)}
    assert {"없는 근거", "근거 없음"} <= kinds


def test_code_check_banned_cliche():
    draft = {"title": "오니 퇴치", "summary": "", "steps": [{"order": 1, "text": "오니를 잡는다",
             "lore_refs": ["enemy_ch1_01"]}], "dialogue": [], "reward": {"text": "", "lore_refs": []}}
    assert any(i["card_id"] == "enemy_ch1_01" for i in code_check(draft, CARDS, 1))


def test_code_check_translation_talisman_replaced_by_earwine():
    """⑦ 확정(11/10): 말이 통하는 수단은 통역 부적이 아니라 귀밝이술이다."""
    draft = {"title": "통역 부적 만들기", "summary": "", "steps": [{"order": 1, "text": "통역 부적을 그린다",
             "lore_refs": ["char_knight_01"]}], "dialogue": [], "reward": {"text": "", "lore_refs": []}}
    assert any(i["card_id"] == "item_earwine_01" for i in code_check(draft, CARDS, 1))


class DropsMachineLLM(FakeLLM):
    """실제 모델처럼 요청의 뒷장 요소(기계 동료)를 슬쩍 빼고 쓰는 작성자."""

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        return super().write(request.replace("기계 동료와 함께 ", ""), cards, chapter, feedback, previous, guide)


def test_later_chapter_in_request_caught_even_if_writer_drops_it(tmp_path):
    app = build_graph(DropsMachineLLM(), CARDS, save_dir=tmp_path)
    r = run_one(app, DEMO[2][1], [], quiet=True)
    assert "기계" not in r["draft"]["summary"]
    assert r["stop_reason"] == "needs_review", r["log"]


class PreFixLLM(FakeLLM):
    """실제 모델처럼 요청의 '바닷가'를 처음부터 설정에 맞게 바꿔 쓰는 작성자."""

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        d = super().write(request.replace("바닷가", "뒷산 계곡"), cards, chapter, feedback, previous, guide)
        d["request_changes"] = ["바닷가 → 뒷산 계곡 (place_village_01)"]
        return d


@pytest.mark.parametrize("llm", [FakeLLM(), PreFixLLM()])
def test_request_conflict_is_reported_to_planner(tmp_path, llm):
    app = build_graph(llm, CARDS, save_dir=tmp_path)
    r = run_one(app, DEMO[1][1], [{"approve": True}], quiet=True)
    assert r["stop_reason"] == "approved", r["log"]
    assert r["request_notes"] and "바닷가" in r["request_notes"][0]
    assert "뒷산 계곡" in r["request_notes"][0]
    assert any("요청 충돌" in line for line in r["log"])


def test_live_log_matches_final_log(app):
    seen = []
    r = run_one(app, DEMO[3][1], DEMO[3][2], quiet=True, on_log=seen.append)
    assert r["stop_reason"] == "approved"
    assert sorted(seen) == sorted(r["log"])  # 두 검수자가 동시에 돌아 순서는 바뀔 수 있다
