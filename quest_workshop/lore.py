"""설정 카드 불러오기와 검색.

지금은 키워드 겹침으로 찾는다(기본 단계). 임베딩 벡터 검색은 확장 단계에서 바꿔 끼운다.
어느 쪽이든 '이 장에 나올 수 없는 카드는 처음부터 뺀다'는 규칙은 같다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CARDS = Path(__file__).resolve().parent.parent / "data" / "lore_ch1.json"


@dataclass
class Card:
    card_id: str
    type: str
    title: str
    chapters: list[int]
    keywords: list[str]
    text: str
    not_present: list[str] = field(default_factory=list)
    spoiler: str = ""

    def for_writer(self) -> dict:
        """작성자에게 주는 모양. 스포일러는 뺀다."""
        return {"card_id": self.card_id, "type": self.type, "title": self.title, "text": self.text}

    def for_reviewer(self) -> dict:
        """검수자에게 주는 모양. 스포일러와 등장 가능 장까지 보여 준다."""
        return {
            "card_id": self.card_id,
            "type": self.type,
            "title": self.title,
            "chapters": self.chapters,
            "text": self.text,
            "not_present": self.not_present,
            "spoiler": self.spoiler,
        }


def load_cards(path: str | Path = DEFAULT_CARDS) -> dict[str, Card]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cards = [Card(**c) for c in raw["cards"]]
    return {c.card_id: c for c in cards}


def retrieve(request: str, cards: dict[str, Card], chapter: int, k: int = 6) -> list[Card]:
    """요청과 키워드가 많이 겹치는 카드부터 k장. 이 장에 못 나오는 카드는 제외."""
    allowed = [c for c in cards.values() if chapter in c.chapters]
    scored = []
    for c in allowed:
        score = sum(1 for kw in c.keywords if kw in request)
        score += 0.5 * sum(1 for kw in c.keywords if kw in c.title and kw in request)
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    hits = [c for s, c in scored if s > 0][:k]
    # 주인공과 세계 규칙은 항상 깔아 둔다(어느 퀘스트든 도사가 주인공).
    for must in ("char_dosa_01", "rule_world_01"):
        if must in cards and chapter in cards[must].chapters and cards[must] not in hits:
            hits.append(cards[must])
    return hits


DEFAULT_MAIN_STORY = Path(__file__).resolve().parent.parent / "data" / "main_story_ch1.json"


def load_main_story(chapter: int | None = None, path: str | Path = DEFAULT_MAIN_STORY) -> list[dict]:
    """메인 시나리오가 맡는 사건 목록. 퀘스트 작업실(서브 퀘스트)은 이 사건을 다시 쓰지 않는다."""
    p = Path(path)
    if not p.exists():
        return []
    beats = json.loads(p.read_text(encoding="utf-8")).get("beats", [])
    return [b for b in beats if chapter is None or b.get("chapter") == chapter]


def unlock_text(unlock: dict | None, beats: list[dict] | None = None) -> str:
    """열림 조건을 사람이 읽는 글로. 예: '어긋난 액막이제 이후 ~ 귀밝이술로 말이 통함 전'"""
    u = unlock or {}
    if not (u.get("after") or u.get("before")):
        return ""
    titles = {b["beat_id"]: b["title"] for b in (load_main_story() if beats is None else beats)}
    a = f"{titles.get(u['after'], u['after'])} 이후" if u.get("after") else "장 시작"
    b = f"{titles.get(u['before'], u['before'])} 전" if u.get("before") else "장 끝"
    return f"{a} ~ {b}" + (f" ({u['reason']})" if u.get("reason") else "")
