"""영구 저장: 파일을 쓸 때 GitHub(별도 브랜치)에도 올리고, 서버가 켜질 때 다시 받아 온다. 가짜 GitHub로 확인."""

from quest_workshop import sync
from quest_workshop.graph import build_graph
from quest_workshop.guide import add_rule, load_rules
from quest_workshop.llm import FakeLLM
from quest_workshop.lore import load_cards
from quest_workshop.run import DEMO, run_one


class FakeGitHub(sync.GitHubSync):
    def __init__(self):
        super().__init__("token")
        self.refs = {"main": "abc"}
        self.files = {}
        self.fail = False

    def _api(self, method, path, body=None):
        if self.fail:
            raise OSError("network down")
        path = path.split("?")[0]
        if path.startswith("/git/ref/heads/"):
            name = path.rsplit("/", 1)[1]
            return {"object": {"sha": self.refs[name]}} if name in self.refs else None
        if path == "/git/refs":
            self.refs[body["ref"].split("/")[-1]] = body["sha"]
            return {}
        if path == "/contents/quests" and method == "GET":
            return [{"type": "file", "name": k.split("/")[1]} for k in self.files] or None
        key = path[len("/contents/"):]
        if method == "GET":
            return {"sha": "s", "content": self.files[key]} if key in self.files else None
        if method == "PUT":
            assert body["branch"] == "quest-data"
            self.files[key] = body["content"]
            return {}


def test_approved_quest_and_rules_go_to_github_branch(tmp_path, monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(sync, "_sync", gh)
    app = build_graph(FakeLLM(), load_cards(), save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": False, "feedback": "이장 말투를 더 겁먹게"}, {"approve": True}],
                quiet=True)
    assert "quest-data" in gh.refs                              # 브랜치를 새로 만들었다
    assert any(k.startswith("quests/quest_") for k in gh.files)  # 승인 퀘스트
    assert "quests/guidelines.json" in gh.files                  # 기획 지침
    assert any("GitHub에도 저장" in line for line in r["log"])


def test_restart_pulls_files_back(tmp_path, monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(sync, "_sync", gh)
    add_rule(tmp_path / "before", "보상은 묻지 않는다")
    fresh = tmp_path / "after_restart"
    assert "1개 파일" in sync.pull_all(fresh)
    assert [r["text"] for r in load_rules(fresh)] == ["보상은 묻지 않는다"]


def test_github_failure_does_not_block_approval(tmp_path, monkeypatch):
    gh = FakeGitHub()
    gh.fail = True
    monkeypatch.setattr(sync, "_sync", gh)
    app = build_graph(FakeLLM(), load_cards(), save_dir=tmp_path)
    r = run_one(app, DEMO[0][1], [{"approve": True}], quiet=True)
    assert r["stop_reason"] == "approved"
    assert any("GitHub 저장 실패" in line for line in r["log"])


def test_disabled_without_token(tmp_path):
    assert sync.persist(tmp_path / "x.json") == "" and sync.pull_all(tmp_path) == ""
