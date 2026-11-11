"""코드로 하는 설정 검사. AI 판단 없이 규칙만으로 막을 수 있는 것을 먼저 막는다.

1. 근거 번호(lore_refs)가 실제로 있는 카드인가
2. 근거로 단 카드가 이 장에 나올 수 있는 카드인가
3. 이 장에 나오면 안 되는 카드(뒷장 요소)의 키워드가 본문에 있는가
4. 카드의 not_present(있으면 안 되는 것)가 본문에 있는가
5. 단계마다 근거가 하나 이상 달려 있는가
6. (request_check) 요청 문장 자체에 뒷장 요소나 not_present가 있는가. 작성자가 슬쩍 빼거나 바꿔 쓰면 초안 검사로는 못 잡는다.
7. (main_check) 메인 시나리오가 맡는 사건(data/main_story_ch1.json)의 금지어가 요청·초안에 있는가.
   서브 퀘스트가 메인 퀘스트를 그대로 다시 하면 안 된다. 뜻이 비슷한 것은 AI 검수자가 본다.
"""
from __future__ import annotations

from .lore import Card


def draft_text(draft: dict) -> str:
    parts = [draft.get("title", ""), draft.get("summary", "")]
    parts += [s.get("text", "") for s in draft.get("steps", [])]
    parts += [d.get("line", "") for d in draft.get("dialogue", [])]
    parts.append(draft.get("reward", {}).get("text", ""))
    return "\n".join(parts)


def all_refs(draft: dict) -> list[str]:
    refs = []
    for s in draft.get("steps", []):
        refs += s.get("lore_refs", [])
    for d in draft.get("dialogue", []):
        refs += d.get("lore_refs", [])
    refs += draft.get("reward", {}).get("lore_refs", [])
    return refs


def main_check(text: str, beats: list[dict], kind: str = "메인 겹침") -> list[dict]:
    issues = []
    for b in beats:
        found = [t for t in b.get("block_terms", []) if t in text]
        if found:
            issues.append({"kind": kind, "card_id": b["beat_id"], "terms": found, "title": b["title"],
                           "detail": f"'{found[0]}'는 메인 시나리오 사건 '{b['title']}'의 몫이라 서브 퀘스트에서 쓰지 않는다."})
    return issues


def unlock_check(draft: dict, beats: list[dict]) -> list[dict]:
    """열림 조건이 실제 메인 사건 번호인지, '이후'가 '이전'보다 앞인지."""
    if not beats:
        return []
    order = {b["beat_id"]: i for i, b in enumerate(beats)}
    u = draft.get("unlock") or {}
    issues = []
    for key in ("after", "before"):
        if u.get(key) and u[key] not in order:
            issues.append({"kind": "열림 조건", "card_id": u[key],
                           "detail": f"열림 조건의 '{u[key]}'는 메인 사건 목록에 없는 번호다."})
    a, b = u.get("after"), u.get("before")
    if a in order and b in order and order[a] >= order[b]:
        issues.append({"kind": "열림 조건", "card_id": b,
                       "detail": f"열림 조건이 '{a}' 이후 ~ '{b}' 이전인데, {b}가 {a}보다 먼저라 열릴 수 없다."})
    return issues


def code_check(draft: dict, cards: dict[str, Card], chapter: int, beats: list[dict] = ()) -> list[dict]:
    issues: list[dict] = []
    text = draft_text(draft)
    issues += main_check(text, list(beats))
    issues += unlock_check(draft, list(beats))

    for ref in sorted(set(all_refs(draft))):
        if ref not in cards:
            issues.append({"kind": "없는 근거", "card_id": ref, "detail": f"근거 번호 {ref}는 설정 카드에 없다."})
        elif chapter not in cards[ref].chapters:
            issues.append({"kind": "뒷장 요소", "card_id": ref,
                           "detail": f"{cards[ref].title}({ref})는 {cards[ref].chapters}장 카드라 {chapter}장 근거로 쓸 수 없다."})

    for c in cards.values():
        if chapter not in c.chapters:
            found = [kw for kw in c.keywords if kw in text]
            if found:
                issues.append({"kind": "뒷장 요소", "card_id": c.card_id, "terms": found,
                               "detail": f"'{found[0]}'는 {c.title}({c.card_id}) 요소이고 {c.chapters}장부터 나온다."})
        for term in c.not_present:
            if term in text:
                issues.append({"kind": "설정 충돌", "card_id": c.card_id, "terms": [term],
                               "detail": f"'{term}'는 {c.title}({c.card_id}) 설정과 맞지 않는다: {c.text[:40]}…"})

    for s in draft.get("steps", []):
        if not s.get("lore_refs"):
            issues.append({"kind": "근거 없음", "card_id": "",
                           "detail": f"{s.get('order', '?')}단계에 근거 카드가 없다."})
    return issues


def request_check(request: str, cards: dict[str, Card], chapter: int, beats: list[dict] = ()) -> list[dict]:
    """요청 문장 자체가 설정과 부딪히는 곳을 찾는다.

    - 뒷장 요소(kind '뒷장 요소(요청)'): 빼면 요청 자체가 달라지므로 충돌로 보고, 재작성으로 풀리지 않아 사람 검토로 간다.
    - not_present(kind '요청 충돌'): 바닷가 → 뒷산처럼 설정에 맞게 바꿔 쓸 수 있다. 충돌로 막지 않고,
      '요청에서 바꿔야 하는 점'으로 기획자에게 알린다. 초안에 그대로 남아 있으면 초안 검사가 충돌로 잡는다.
    """
    issues = main_check(request, list(beats), kind="메인 겹침(요청)")
    for c in cards.values():
        if chapter not in c.chapters:
            found = [kw for kw in c.keywords if kw in request]
            if found:
                issues.append({"kind": "뒷장 요소(요청)", "card_id": c.card_id, "terms": found,
                               "detail": f"요청의 '{found[0]}'는 {c.title}({c.card_id}) 요소이고 {c.chapters}장부터 나온다."})
        found = [t for t in c.not_present if t in request]
        if found:
            issues.append({"kind": "요청 충돌", "card_id": c.card_id, "terms": found,
                           "detail": f"요청의 '{found[0]}'는 {c.title}({c.card_id}) 설정과 맞지 않는다: {c.text[:40]}…"})
    return issues
