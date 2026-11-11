"""퀘스트 작업실 그래프 (기본 단계 + 설정 구멍).

요청 → 설정 검색 → 초안 작성 → [설정 검수(코드 + AI) ‖ 기획 검수(AI)] 동시에, 서로 안 보고 → 판정
  판정: 통과 → 사람 승인 / 충돌 → 재작성(최대 1회) / 재작성 후에도 충돌 → needs_review / 설정 비어 있음 → lore_gap
  기획 검수(재미·흐름·말투)는 가치 판단이라 재작성을 강제하지 않는다. 승인 때 기획자에게 보여 주고,
  설정 충돌로 다시 쓸 때는 기획 의견도 함께 넘긴다.
사람 승인: 승인 → 저장(approved) / 의견과 함께 거절 → 재작성 → 다시 검수 / 의견 없이 거절 → rejected
메인 시나리오 사건(data/main_story_ch1.json)은 서브 퀘스트에서 다시 쓰지 않는다: 코드 금지어 + AI 검수.
기획자 의견은 지침(guidelines.json)으로 쌓여 이후 모든 퀘스트의 제안·작성·기획 검수에 반영된다.
"""
from __future__ import annotations

import json
import operator
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .checks import code_check, request_check
from .guide import add_rule, rule_texts
from .sync import persist
from .lore import Card, load_cards, load_main_story, retrieve

MAX_REWRITES = 1          # 검수 충돌로 다시 쓰는 횟수
MAX_HUMAN_ROUNDS = 3      # 기획자 거절 → 다시 쓰기 반복 한도
StopReason = Literal["approved", "rejected", "needs_review", "lore_gap"]


class QuestState(TypedDict, total=False):
    request: str
    chapter: int
    card_ids: list[str]
    draft: dict
    code_issues: list[dict]
    review: dict
    plan_review: dict
    verdict: str              # pass / rewrite / needs_review / lore_gap
    rewrite_count: int
    human_rounds: int
    feedback: list[str]       # 다음 초안에 반영할 의견
    human_feedback: str
    stop_reason: StopReason
    lore_gaps: list[str]
    request_notes: list[str]  # 요청이 설정과 부딪혀 바꿔 쓴 점. 승인 때 기획자에게 보여 준다
    saved_path: str
    log: Annotated[list[str], operator.add]


def build_graph(llm, cards: dict[str, Card] | None = None, save_dir: str | Path = "output/quests",
                checkpointer=None, beats: list[dict] | None = None):
    cards = cards or load_cards()
    save_dir = Path(save_dir)
    all_beats = load_main_story() if beats is None else beats

    def chapter_beats(state) -> list[dict]:
        return [b for b in all_beats if b.get("chapter") == state.get("chapter", 1)]

    def guide(state) -> dict:
        """작성자·검수자에게 함께 주는 기획 지침: 메인 사건, 쌓인 기획자 지침, 이 퀘스트에 준 기획자 의견."""
        return {"main_beats": [{"beat_id": b["beat_id"], "title": b["title"], "summary": b["summary"]}
                               for b in chapter_beats(state)],
                "rules": rule_texts(save_dir), "human_feedback": state.get("human_feedback", "")}

    def search(state: QuestState):
        hits = retrieve(state["request"], cards, state.get("chapter", 1))
        ids = [c.card_id for c in hits]
        return {"card_ids": ids, "rewrite_count": 0, "human_rounds": 0, "feedback": [],
                "log": [f"[검색] 카드 {len(ids)}장: {', '.join(ids)}"]}

    def write(state: QuestState):
        writer_cards = [cards[i].for_writer() for i in state["card_ids"]]
        draft = llm.write(state["request"], writer_cards, state.get("chapter", 1),
                          state.get("feedback", []), state.get("draft"), guide=guide(state))
        n = state.get("rewrite_count", 0) + state.get("human_rounds", 0)
        label = "초안" if n == 0 else f"재작성 {n}"
        return {"draft": draft, "log": [f"[작성] {label}: {draft.get('title', '')}"]}

    def lore_review(state: QuestState):
        ch = state.get("chapter", 1)
        beats = chapter_beats(state)
        issues = code_check(state["draft"], cards, ch, beats)
        seen = {i["card_id"] for i in issues if i["kind"] in ("뒷장 요소", "메인 겹침")}
        req = request_check(state["request"], cards, ch, beats)
        issues += [i for i in req if i["kind"] in ("뒷장 요소(요청)", "메인 겹침(요청)") and i["card_id"] not in seen]
        req_conflicts = [i for i in req if i["kind"] == "요청 충돌"]
        changed = ", ".join(state["draft"].get("request_changes", [])) or "작성자가 바꾼 내용을 적지 않음"
        notes = [f"요청의 '{i['terms'][0]}'는 {i['card_id']} 설정과 맞지 않음 → 바꿔 씀: {changed}" for i in req_conflicts]
        review = llm.review(state["draft"], [c.for_reviewer() for c in cards.values()], ch, guide=guide(state))
        n_ai = len(review.get("issues", []))
        log = [f"[설정 검수] 코드 검사 {len(issues)}건, AI 검수 '{review.get('verdict')}' {n_ai}건"]
        if state.get("rewrite_count", 0) == 0 and state.get("human_rounds", 0) == 0:
            log += [f"   · 요청 충돌: {i['detail']}" for i in req_conflicts]
        return {"code_issues": issues, "review": review, "request_notes": notes, "log": log}

    def plan_review(state: QuestState):
        ch = state.get("chapter", 1)
        pr = llm.plan_review(state["draft"], state["request"],
                             [cards[i].for_writer() for i in state["card_ids"]], ch, guide=guide(state))
        return {"plan_review": pr,
                "log": [f"[기획 검수] {pr.get('verdict')}" + (f" {len(pr.get('notes', []))}건" if pr.get("notes") else "")]
                       + [f"   + 잘된 점: {g}" for g in pr.get("good", [])]
                       + [f"   - 고칠 점: {n['point']} → {n['fix']}" for n in pr.get("notes", [])]}

    def to_reviewers(state: QuestState):
        # 두 검수자가 같은 초안을 동시에, 서로의 결과를 보지 않고 검수한다
        return [Send("lore_review", state), Send("plan_review", state)]

    def judge(state: QuestState):
        draft, review, issues = state["draft"], state["review"], state["code_issues"]
        gaps = list(draft.get("questions", []))
        if review.get("verdict") == "설정에 없음":
            gaps += [i["problem"] for i in review.get("issues", [])]
        conflicts = [i["detail"] for i in issues]
        if review.get("verdict") == "충돌":
            conflicts += [f"{i['problem']} → {i['fix']}" for i in review.get("issues", [])]

        if gaps and not conflicts:
            return {"verdict": "lore_gap", "lore_gaps": gaps, "stop_reason": "lore_gap",
                    "log": [f"[판정] 설정 구멍 {len(gaps)}개 → 기획자에게 질문으로 돌려줌"]}
        plan_notes = [f"(기획 의견) {n['point']} → {n['fix']}" for n in state.get("plan_review", {}).get("notes", [])]
        if conflicts:
            if state.get("rewrite_count", 0) < MAX_REWRITES:
                return {"verdict": "rewrite", "feedback": conflicts + plan_notes,
                        "rewrite_count": state.get("rewrite_count", 0) + 1,
                        "log": [f"[판정] 충돌 {len(conflicts)}건 → 1회 재작성"] + [f"   · {c}" for c in conflicts]}
            return {"verdict": "needs_review", "stop_reason": "needs_review", "lore_gaps": gaps,
                    "log": [f"[판정] 재작성 후에도 충돌 {len(conflicts)}건 → 사람 검토 필요로 멈춤"]
                           + [f"   · {c}" for c in conflicts]}
        notes = state.get("request_notes", [])
        plan_log = ([f"   · 기획 의견 {len(plan_notes)}건은 고칠지 기획자가 승인 때 판단"] if plan_notes else [])
        if notes:
            return {"verdict": "pass",
                    "log": [f"[판정] 통과. 요청을 설정에 맞게 바꾼 점 {len(notes)}건 → 승인 때 기획자에게 알림"]
                           + [f"   · {n}" for n in notes] + plan_log}
        return {"verdict": "pass", "log": ["[판정] 통과 → 기획자 승인 대기"] + plan_log}

    def human(state: QuestState):
        answer = interrupt({"draft": state["draft"], "review": state["review"],
                            "code_issues": state["code_issues"],
                            "plan_review": state.get("plan_review", {}),
                            "request_notes": state.get("request_notes", [])})
        approve = bool(answer.get("approve"))
        fb = (answer.get("feedback") or "").strip()
        if approve:
            return {"human_feedback": "", "log": ["[기획자] 승인"]}
        if fb:
            add_rule(save_dir, fb, source=state["request"])   # 이 퀘스트뿐 아니라 이후 퀘스트에도 반영
        if fb and state.get("human_rounds", 0) < MAX_HUMAN_ROUNDS:
            return {"human_feedback": fb, "feedback": [f"기획자 의견: {fb}"],
                    "human_rounds": state.get("human_rounds", 0) + 1,
                    "log": [f"[기획자] 거절, 의견 반영해 다시 쓰기: {fb}"]}
        return {"human_feedback": fb, "stop_reason": "rejected", "log": ["[기획자] 거절 → 종료"]}

    def save(state: QuestState):
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"quest_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
        record = {"request": state["request"], "chapter": state.get("chapter", 1),
                  "draft": state["draft"], "review": state["review"],
                  "plan_review": state.get("plan_review", {}),
                  "request_notes": state.get("request_notes", []), "stop_reason": "approved"}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        remote = persist(path)
        return {"stop_reason": "approved", "saved_path": str(path),
                "log": [f"[저장] {path}" + (f" ({remote})" if remote else "")]}

    def after_judge(state: QuestState):
        return {"pass": "human", "rewrite": "write"}.get(state["verdict"], END)

    def after_human(state: QuestState):
        if state.get("stop_reason") == "rejected":
            return END
        return "write" if state.get("human_feedback") else "save"

    g = StateGraph(QuestState)
    g.add_node("search", search)
    g.add_node("write", write)
    g.add_node("lore_review", lore_review)
    g.add_node("plan_review", plan_review)
    g.add_node("judge", judge)
    g.add_node("human", human)
    g.add_node("save", save)
    g.add_edge(START, "search")
    g.add_edge("search", "write")
    g.add_conditional_edges("write", to_reviewers, ["lore_review", "plan_review"])
    g.add_edge(["lore_review", "plan_review"], "judge")   # 둘 다 끝나야 판정
    g.add_conditional_edges("judge", after_judge, ["human", "write", END])
    g.add_conditional_edges("human", after_human, ["write", "save", END])
    g.add_edge("save", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
