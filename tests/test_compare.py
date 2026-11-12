"""A/B/C 비교: 가짜 모델로 세 구성이 끝까지 돌고, 표가 만들어진다."""
from quest_workshop.compare import CONFIGS, REQUESTS, compare, summary, to_markdown
from quest_workshop.llm import FakeLLM


def test_compare_runs_all_configs_and_makes_table():
    result = compare(FakeLLM(), on_log=lambda *_: None)
    assert len(result["rows"]) == len(REQUESTS) * len(CONFIGS)
    s = summary(result)
    assert s["B"]["ok"] == s["C"]["ok"] == len(REQUESTS)      # 검수가 붙으면 기대대로 끝난다
    assert s["A"]["code"] > 0 and s["B"]["code"] == 0          # 작성자만이면 설정 문제가 그대로 나간다
    assert s["C"]["plan"] > 0 and s["B"]["plan"] == 0          # 기획 의견은 C에만
    assert s["A"]["calls"] < s["B"]["calls"] < s["C"]["calls"]  # 대신 호출이 늘어난다
    md = to_markdown(result)
    assert "| A 작성자만 |" in md and "## 요청별" in md


def test_compare_does_not_save_quests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    compare(FakeLLM(), on_log=lambda *_: None)
    assert not list(tmp_path.rglob("quest_*.json"))
