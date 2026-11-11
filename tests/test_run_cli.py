"""터미널 실행(run.py)이 끝까지 오류 없이 도는지. 가짜 모델로 화면 출력까지 거친다."""
from quest_workshop.run import main


def test_propose_cli_runs_to_end(tmp_path, capsys):
    assert main(["--llm", "fake", "--propose", "3", "--yes", "--save-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[기획 검수]" in out and "오늘 승인 3개" in out


def test_demo_cli_runs_to_end(tmp_path, capsys):
    assert main(["--llm", "fake", "--demo", "--save-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("종료 사유") == 5 and "오류" not in out


def test_demo_saves_apart_from_real_quests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["--llm", "fake", "--demo"])
    assert list((tmp_path / "output" / "demo").glob("quest_*.json"))
    assert not (tmp_path / "output" / "quests").exists()
