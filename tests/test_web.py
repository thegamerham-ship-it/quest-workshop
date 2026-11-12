"""웹 화면(streamlit_app.py): 가짜 모델로 제안 받기 → 승인 → 저장, 요청 직접 넣기."""
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")


@pytest.fixture
def at(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEST_LLM", "fake")
    monkeypatch.setenv("QUEST_SAVE_DIR", str(tmp_path))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    a = st_testing.AppTest.from_file("../streamlit_app.py", default_timeout=30)
    a.run()
    return a


def click(at, label):
    next(b for b in at.button if b.label.startswith(label)).click().run()


def test_propose_then_approve_saves_quest(at, tmp_path):
    assert not at.exception
    click(at, "오늘의 제안 받기")
    assert not at.exception
    items = at.session_state["items"]
    assert len(items) == 5 and all(v["status"] == "승인 대기" for v in items.values())
    click(at, "승인")
    assert not at.exception
    assert sum(v["status"] == "approved" for v in at.session_state["items"].values()) == 1
    assert len(list(tmp_path.glob("quest_*.json"))) == 1


def test_direct_request_flow(at):
    at.text_input[0].set_value("뒷산 바위 제단의 유래를 알아보는 퀘스트").run()
    click(at, "작성·검수 시작")
    assert not at.exception
    (item,) = at.session_state["items"].values()
    assert item["status"] == "lore_gap" and item["lore_gaps"]


def test_password_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEST_LLM", "fake")
    monkeypatch.setenv("QUEST_SAVE_DIR", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "secret")
    a = st_testing.AppTest.from_file("../streamlit_app.py", default_timeout=30)
    a.run()
    assert not any(b.label.startswith("오늘의 제안") for b in a.button)


def test_discard_and_get_new_proposal(at, tmp_path):
    click(at, "오늘의 제안 받기")
    before = {v["request"] for v in at.session_state["items"].values()}
    click(at, "버리고 새 제안")
    assert not at.exception
    after = {v["request"] for v in at.session_state["items"].values()}
    assert len(after) == 5 and len(before - after) == 1 and len(after - before) == 1
    assert (tmp_path / "discarded.json").exists()


def test_browse_without_password(tmp_path, monkeypatch):
    """암호 없이 둘러보기: 가짜 모델로 흐름이 돌고, 실제 저장 폴더는 건드리지 않는다. 실제 승인 퀘스트는 보인다."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "quest_real.json").write_text(
        '{"request": "실제 요청", "draft": {"title": "실제 퀘스트"}}', encoding="utf-8")
    monkeypatch.setenv("QUEST_LLM", "gemini")      # 서버 기본이 Gemini여도 둘러보기는 가짜 모델
    monkeypatch.setenv("QUEST_SAVE_DIR", str(real))
    monkeypatch.setenv("APP_PASSWORD", "secret")
    a = st_testing.AppTest.from_file("../streamlit_app.py", default_timeout=30)
    a.run()
    click(a, "둘러보기 시작")
    assert not a.exception
    browse = a.session_state["browse_dir"]
    assert (Path(browse) / "quest_real.json").exists()
    click(a, "오늘의 제안 받기")
    click(a, "승인")
    assert not a.exception
    assert len(list(Path(browse).glob("quest_*.json"))) == 2
    assert [p.name for p in real.glob("quest_*.json")] == ["quest_real.json"]


def test_variants_from_approved_quest(at, tmp_path):
    click(at, "오늘의 제안 받기")
    click(at, "승인")
    at.run()
    click(at, "변형 만들기")
    assert not at.exception
    (path,) = tmp_path.glob("quest_*.json")
    res = at.session_state[f"var_{path.name}"]
    assert len(res["variants"]) == 3 and all(v["lore_ok"] for v in res["variants"])


def test_model_quota_error_shows_notice_not_crash(at, monkeypatch):
    from quest_workshop.llm import FakeLLM

    def over_quota(*a, **k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    monkeypatch.setattr(FakeLLM, "propose", over_quota)
    click(at, "오늘의 제안 받기")
    assert not at.exception
    assert any("한도" in w.value for w in at.warning)
