import pytest

from quest_workshop import sync


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """테스트가 실제 GitHub에 쓰지 않게: 영구 저장을 끈다(필요한 테스트만 가짜 GitHub를 켠다)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(sync, "_sync", None)
    monkeypatch.setattr(sync, "_configured", True)
