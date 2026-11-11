"""실행: 요청 하나를 넣고 승인/거절을 직접 입력한다.

  python -m quest_workshop.run "이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트"
  python -m quest_workshop.run --llm gemini "..."
  python -m quest_workshop.run --demo        # 테스트 요청 5개를 자동으로 돌린다
  python -m quest_workshop.run --propose 5   # 제안 에이전트가 오늘의 요청 5개를 만들고 각각 작성·검수
  python -m quest_workshop.run --propose 5 --yes   # 승인 대기인 것을 모두 승인(데모용)
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

from langgraph.types import Command

from .graph import build_graph
from .llm import make_llm
from .lore import load_cards, unlock_text
from .propose import answer, build_batch_graph

DEMO = [
    ("1. 바로 통과", "이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트", [{"approve": True}]),
    ("2. 요청이 설정과 충돌 → 바꿔 씀 → 기획자에게 알리고 통과", "이장이 바닷가에서 떠내려온 갑옷을 찾아 달라는 퀘스트", [{"approve": True}]),
    ("3. 뒷장 요소 → 검토 필요", "도사가 기계 동료와 함께 기사를 구하는 퀘스트", []),
    ("4. 기획자 거절 → 의견 반영 → 승인", "이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트",
     [{"approve": False, "feedback": "이장 말투를 더 겁먹은 듯이"}, {"approve": True}]),
    ("5. 설정 구멍", "뒷산 바위 제단의 유래를 알아보는 퀘스트", []),
]


def show_draft(d: dict) -> None:
    print(f"  제목: {d.get('title')}  /  의뢰인: {d.get('giver')}")
    if d.get("intent"):
        print(f"   의도: {d['intent']}")
    if unlock_text(d.get("unlock")):
        print(f"   열림 조건: {unlock_text(d.get('unlock'))}")
    for s in d.get("steps", []):
        print(f"   {s['order']}. {s['text']}  {s.get('lore_refs')}")
    for l in d.get("dialogue", []):
        direction = f"  ({l['direction']})" if l.get("direction") else ""
        print(f"   {l['speaker']}: \"{l['line']}\"{direction}")
    print(f"   보상: {d.get('reward', {}).get('text')}")
    for c in d.get("request_changes", []):
        print(f"   요청에서 바꾼 점: {c}")


def show_plan(pr: dict) -> None:
    if not pr:
        return
    print(f"  [기획 검수] {pr.get('verdict')}")
    for g in pr.get("good", []):
        print(f"   + {g}")
    for n in pr.get("notes", []):
        print(f"   - {n['point']} → {n['fix']}")


def _invoke(app, inp, cfg, on_log=None) -> dict:
    """on_log가 있으면 노드가 끝날 때마다 로그를 바로 보여 준다(어디서 기다리는지 보이게)."""
    if on_log is None:
        return app.invoke(inp, cfg)
    interrupts = []
    for chunk in app.stream(inp, cfg, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                interrupts.extend(update)
            elif isinstance(update, dict):
                for line in update.get("log", []):
                    on_log(line)
    result = dict(app.get_state(cfg).values)
    if interrupts:
        result["__interrupt__"] = interrupts
    return result


def run_one(app, request: str, answers=None, chapter: int = 1, quiet: bool = False, on_log=None) -> dict:
    """answers가 있으면 그 순서대로 승인/거절, 없으면 터미널에서 묻는다."""
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    answers = list(answers) if answers is not None else None
    result = _invoke(app, {"request": request, "chapter": chapter}, cfg, on_log)
    while "__interrupt__" in result:
        if not quiet:
            print("\n  ── 기획자 승인 대기 ──")
            payload = result["__interrupt__"][0].value
            for n in payload.get("request_notes", []):
                print(f"  ! 요청 충돌: {n}")
            show_draft(payload["draft"])
            show_plan(payload.get("plan_review", {}))
        if answers is not None:
            ans = answers.pop(0) if answers else {"approve": False}
        else:
            yn = input("  승인할까요? (y / 고칠 점 입력 / 빈칸=거절): ").strip()
            ans = {"approve": True} if yn.lower() in ("y", "yes", "ㅇ") else {"approve": False, "feedback": yn}
        result = _invoke(app, Command(resume=ans), cfg, on_log)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="퀘스트 작업실")
    p.add_argument("request", nargs="?")
    p.add_argument("--llm", default="auto", choices=["auto", "fake", "gemini"])
    p.add_argument("--chapter", type=int, default=1)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--propose", type=int, metavar="N", help="제안 에이전트가 요청 N개를 만든다")
    p.add_argument("--yes", action="store_true", help="--propose에서 승인 대기인 것을 모두 승인")
    p.add_argument("--save-dir", default=None,
                   help="승인한 퀘스트를 저장할 폴더. 기본: 실제 작업은 output/quests, --demo는 output/demo")
    p.add_argument("--parallel", type=int, default=2,
                   help="--propose에서 동시에 작성·검수할 개수. 무료 키는 분당 호출 한도가 있어 작게 둔다")
    a = p.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    llm = make_llm(a.llm)
    if a.save_dir is None:
        # 데모로 승인한 테스트 퀘스트가 실제 기록에 섞이면 제안 에이전트가 '이미 만든 퀘스트'로 착각한다
        a.save_dir = "output/demo" if a.demo else "output/quests"
    cards = load_cards()
    app = build_graph(llm, cards, save_dir=a.save_dir)
    print(f"모델: {llm.name}")

    if a.demo:
        for name, req, answers in DEMO:
            print(f"\n=== {name} ===\n요청: {req}", flush=True)
            try:
                r = run_one(app, req, answers, a.chapter, quiet=True,
                            on_log=lambda line: print("  " + line, flush=True))
            except Exception as e:  # 한 요청이 실패해도 나머지는 계속 돈다
                print(f"  ✗ 오류로 이 요청은 건너뜀: {type(e).__name__}: {str(e)[:200]}")
                continue
            print(f"  → 종료 사유: {r.get('stop_reason')}")
            if r.get("lore_gaps"):
                print("  설정 구멍:", *r["lore_gaps"], sep="\n   - ")
        return 0

    if a.propose:
        return run_propose(app, llm, cards, a)

    if not a.request:
        p.print_help()
        return 1
    r = run_one(app, a.request, None, a.chapter)
    print()
    for line in r["log"]:
        print(line)
    print(f"종료 사유: {r.get('stop_reason')}")
    if r.get("lore_gaps"):
        print("설정 구멍:", *r["lore_gaps"], sep="\n - ")
    if r.get("stop_reason") == "needs_review":
        print("남은 충돌:", json.dumps(r.get("code_issues"), ensure_ascii=False, indent=1))
    return 0


def run_propose(app, llm, cards, a) -> int:
    batch = build_batch_graph(llm, app, cards, save_dir=a.save_dir)
    print(f"[제안] 요청 {a.propose}개를 만들고, {a.parallel}개씩 동시에 작성·검수합니다. 몇 분 걸릴 수 있어요.", flush=True)
    out = {"log": [], "results": [], "needed_lore": []}
    for chunk in batch.stream({"chapter": a.chapter, "n": a.propose}, {"max_concurrency": a.parallel},
                              stream_mode="updates"):
        for node, update in chunk.items():
            if not isinstance(update, dict):
                continue
            for line in update.get("log", []):
                print(line, flush=True)
            out["needed_lore"] = update.get("needed_lore", out["needed_lore"])
            for r in update.get("results", []):
                out["results"].append(r)
                print(f"  ✓ {len(out['results'])}개 완료: {r['request']} → {r['status']}", flush=True)
    results = out["results"]
    for i, r in enumerate(results, 1):
        print(f"\n=== 제안 {i}. {r['request']} ===")
        print(f"  이유: {r['why']}")
        for line in r["log"]:
            print("  " + line)
        print(f"  → 상태: {r['status']}")
        for g in r["lore_gaps"]:
            print(f"   - 설정 구멍: {g}")

    waiting = [r for r in results if r["status"] == "승인 대기"]
    print(f"\n승인 대기 {len(waiting)}개 / 제안 {len(results)}개")
    approved = 0
    for r in waiting:
        print(f"\n── 승인 대기: {r['request']}")
        for n in r["request_notes"]:
            print(f"  ! 요청 충돌: {n}")
        show_draft(r["draft"])
        show_plan(r.get("plan_review", {}))
        tid = r["thread_id"]
        res = answer(app, tid, {"approve": True}) if a.yes else None
        while res is None or "__interrupt__" in res:
            if res is not None:
                show_draft(res["__interrupt__"][0].value["draft"])
            yn = input("  승인할까요? (y / 고칠 점 입력 / 빈칸=거절): ").strip()
            ans = {"approve": True} if yn.lower() in ("y", "yes", "ㅇ") else {"approve": False, "feedback": yn}
            res = answer(app, tid, ans)
        print(f"  → {res.get('stop_reason')}  {res.get('saved_path', '')}")
        approved += res.get("stop_reason") == "approved"
    print(f"\n오늘 승인 {approved}개")
    if out.get("needed_lore"):
        print("소재가 모자랍니다. 이런 설정을 추가하면 더 만들 수 있어요:")
        for x in out["needed_lore"]:
            print(f" - {x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
