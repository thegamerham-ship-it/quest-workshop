"""제안 에이전트: "오늘 퀘스트 N개"를 받으면 1장 설정과 이미 만든 퀘스트를 보고 요청 N개를 스스로 만든다.

흐름(LangGraph)
  제안 → (Send로 요청마다 나눠서 동시에) 작성·설정 검수 → 기획자 승인 대기 목록
  - 제안 단계에서 요청 문장에 뒷장 요소가 있거나 이미 만든 요청과 (거의) 같으면 걸러 낸다.
  - 소재가 모자라면 억지로 채우지 않는다. 새 퀘스트가 N개보다 적으면 "필요한 설정"을 기획자에게 보고한다.
    (뜻이 비슷한지는 제안 AI가 판단하고, 코드는 글자가 거의 같은 것만 잡는다.)
  - 각 요청은 기존 퀘스트 작업실 그래프(graph.py)를 그대로 탄다. 승인은 요청마다 따로 한다.
"""
from __future__ import annotations

import json
import operator
import uuid
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from .checks import request_check
from .guide import load_discards, rule_texts
from .lore import Card, load_main_story


def load_existing(save_dir: str | Path) -> list[dict]:
    """이미 승인돼 저장된 퀘스트. 제안할 때 겹치지 않게 보여 준다."""
    out = []
    for p in sorted(Path(save_dir).glob("quest_*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d = rec.get("draft", {})
        refs = set()
        for part in d.get("steps", []) + d.get("dialogue", []) + [d.get("reward", {})]:
            refs.update(part.get("lore_refs", []))
        out.append({"request": rec.get("request", ""), "title": d.get("title", ""),
                    "summary": d.get("summary", ""), "cards": sorted(refs)})
    return out


def card_usage(existing: list[dict]) -> dict[str, int]:
    """카드별로 승인된 퀘스트에 몇 번 쓰였는지."""
    use: dict[str, int] = {}
    for e in existing:
        for cid in e.get("cards", []):
            use[cid] = use.get(cid, 0) + 1
    return use


def _bigrams(s: str) -> set[str]:
    s = s.replace(" ", "").replace("퀘스트", "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    """글자 두 개씩 겹치는 비율(0~1). 거의 같은 문장만 잡는 용도라 뜻이 비슷한 건 못 잡는다."""
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / len(A | B) if A and B else 0.0


ALWAYS = {"char_dosa_01", "rule_world_01"}  # 어느 퀘스트에나 깔리는 카드(주인공, 세계 규칙). 많이 썼다고 보지 않는다
SIMILAR = 0.45  # 이 이상이면 사실상 같은 요청으로 본다


class BatchState(TypedDict, total=False):
    chapter: int
    n: int
    proposals: list[dict]
    needed_lore: list[str]
    pending: list[str]        # 지금 화면에 승인 대기 중인 요청. 겹치지 않게 제안한다
    results: Annotated[list[dict], operator.add]
    log: Annotated[list[str], operator.add]


def build_batch_graph(llm, quest_app, cards: dict[str, Card], save_dir: str | Path = "output/quests",
                      beats: list[dict] | None = None):
    all_beats = load_main_story() if beats is None else beats

    def propose(state: BatchState):
        ch, n = state.get("chapter", 1), state.get("n", 5)
        ch_beats = [b for b in all_beats if b.get("chapter") == ch]
        discards = load_discards(save_dir)
        discarded = [d["request"] for d in discards]
        guide = {"main_beats": [{"beat_id": b["beat_id"], "title": b["title"], "summary": b["summary"]}
                                for b in ch_beats],
                 "rules": rule_texts(save_dir),
                 "discarded": [d["request"] + (f" (버린 이유: {d['reason']})" if d.get("reason") else "")
                               for d in discards]}
        existing = load_existing(save_dir) + [{"request": r, "title": "", "summary": "(아직 승인 대기 중)", "cards": []}
                                              for r in state.get("pending", [])]
        use = card_usage(existing)
        chapter_cards = [{**c.for_writer(), "used_in_quests": use.get(c.card_id, 0)}
                         for c in cards.values() if ch in c.chapters]
        res = llm.propose(chapter_cards, ch, existing, n, guide=guide)
        raw, needed = res.get("items", []), list(res.get("needed_lore", []))
        seen = [e["request"] for e in existing] + [e["title"] for e in existing if e.get("title")] + discarded
        kept, log = [], [f"[제안] 이미 만든 퀘스트 {len(existing)}개를 보고 요청 {len(raw)}개 제안"]
        if use:
            top = sorted(((k, v) for k, v in use.items() if k not in ALWAYS), key=lambda x: -x[1])[:3]
            log.append("   · 많이 쓴 카드: " + ", ".join(f"{cid} {k}회" for cid, k in top))
        for p in raw:
            req = p.get("request", "").strip()
            checked = request_check(req, cards, ch, ch_beats)
            later = [i for i in checked if i["kind"] == "뒷장 요소(요청)"]
            main = [i for i in checked if i["kind"] == "메인 겹침(요청)"]
            close = max(((similarity(req, s), s) for s in seen), default=(0.0, ""))
            if not req:
                continue
            if close[0] >= SIMILAR:
                log.append(f"   · 걸러 냄(이미 있는 것과 거의 같음 {close[0]:.2f}): {req}  ≈ {close[1]}")
            elif later:
                log.append(f"   · 걸러 냄(뒷장 요소): {req}")
            elif main:
                log.append(f"   · 걸러 냄(메인 퀘스트 몫: {main[0]['title']}): {req}")
            else:
                seen.append(req)
                kept.append(p)
        kept = kept[:n]
        if len(kept) < n:
            log.append(f"[제안] 새 소재 부족: {n}개 중 {len(kept)}개만 새로움 → 억지로 채우지 않음")
            if not needed:
                needed = ["제안 AI가 이유를 적지 않았다. 덜 쓴 카드를 넓히거나 새 설정 카드를 추가해 보자."]
            log += [f"   · 필요한 설정: {x}" for x in needed]
        else:
            needed = []
        return {"proposals": kept, "needed_lore": needed, "log": log}

    def fan_out(state: BatchState):
        if not state.get("proposals"):
            return END
        return [Send("draft_one", {"proposal": p, "chapter": state.get("chapter", 1)})
                for p in state["proposals"]]

    def draft_one(payload: dict):
        p = payload["proposal"]
        tid = str(uuid.uuid4())
        r = quest_app.invoke({"request": p["request"], "chapter": payload["chapter"]},
                             {"configurable": {"thread_id": tid}})
        waiting = "__interrupt__" in r
        return {"results": [{
            "request": p["request"], "why": p.get("why", ""), "thread_id": tid,
            "status": "승인 대기" if waiting else r.get("stop_reason"),
            "draft": r.get("draft", {}), "request_notes": r.get("request_notes", []),
            "plan_review": r.get("plan_review", {}),
            "lore_gaps": r.get("lore_gaps", []), "log": r.get("log", []),
        }]}

    g = StateGraph(BatchState)
    g.add_node("propose", propose)
    g.add_node("draft_one", draft_one)
    g.add_edge(START, "propose")
    g.add_conditional_edges("propose", fan_out, ["draft_one", END])
    g.add_edge("draft_one", END)
    return g.compile()


def answer(quest_app, thread_id: str, ans: dict) -> dict:
    """승인 대기 중인 퀘스트 하나에 기획자 답(승인/거절/의견)을 보낸다."""
    return quest_app.invoke(Command(resume=ans), {"configurable": {"thread_id": thread_id}})
