"""모델 호출 오류를 기획자가 읽을 수 있는 말로 바꾼다. 웹이 빨간 오류 화면으로 죽지 않게."""

QUOTA = ("429", "resource_exhausted", "resourceexhausted", "quota", "rate limit", "ratelimit")
BAD_KEY = ("api key not valid", "api_key_invalid", "permission_denied", "permissiondenied",
           "unauthenticated", "401", "403")
TIMEOUT = ("timeout", "timed out", "deadline")


def classify(err: BaseException | str) -> str:
    """quota(오늘 한도 참) / key(키가 막힘) / timeout(응답 없음) / other."""
    text = f"{type(err).__name__} {err}".lower() if isinstance(err, BaseException) else str(err).lower()
    for kind, words in (("quota", QUOTA), ("key", BAD_KEY), ("timeout", TIMEOUT)):
        if any(w in text for w in words):
            return kind
    return "other"


MESSAGES = {
    "quota": "오늘 무료 사용 한도가 찼어요. 내일 다시 시도하거나, '승인된 퀘스트' 탭에서 저장된 결과를 둘러보세요.",
    "key": "AI 키가 막혔거나 바뀌었어요. 지금은 '승인된 퀘스트' 탭에서 저장된 결과를 둘러볼 수 있어요.",
    "timeout": "AI 응답이 늦어 멈췄어요. 잠시 뒤 다시 눌러 주세요.",
    "other": "AI를 부르다 문제가 생겼어요. 잠시 뒤 다시 눌러 주세요.",
}


def friendly(err: BaseException | str) -> str:
    return MESSAGES[classify(err)]
