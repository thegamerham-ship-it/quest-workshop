"""모델 오류 → 기획자용 안내 문구."""
from quest_workshop.errors import classify, friendly


def test_quota_and_key_and_timeout_are_told_apart():
    assert classify(RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota")) == "quota"
    assert classify(ValueError("400 API key not valid. Please pass a valid API key.")) == "key"
    assert classify(TimeoutError("Request timed out")) == "timeout"
    assert classify(KeyError("draft")) == "other"
    assert "한도" in friendly("429 Too Many Requests") and "승인된 퀘스트" in friendly("429")
