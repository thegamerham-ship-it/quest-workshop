"""A/B/C 비교: 검수자를 붙이면 정말 나아지는가.

같은 요청 5개를 세 가지 구성으로 돌리고, 마지막에 같은 기준으로 점검한다.
  A 작성자만          : 초안을 그대로 내보낸다
  B 설정 검수 + 재작성 : 설정 검수(코드 + AI) → 충돌이면 1회 재작성 → 그래도 충돌이면 멈춤
  C 두 검수자 + 판정   : B에 기획 검수를 동시에 붙인 지금의 작업실

점검(세 구성 모두 같은 기준)
  - 기대대로 끝남: 내보내야 할 요청은 설정 문제 없이 내보냈는가, 멈춰야 할 요청(뒷장 요소·설정에 없는 것)은 멈췄는가
  - 내보낸 초안의 설정 문제: 코드 검사(뒷장 요소, 있으면 안 되는 것, 없는 근거 번호, 메인 겹침). 기계 기준이라 공정하다
  - AI 점검: 설정 검수자 프롬프트로 내보낸 초안을 다시 본 결과(같은 모델이라 참고용)
  - 기획 의견: 승인 전에 기획자에게 보여 준 고칠 점 수
  - 모델 호출 수, 걸린 시간

  python -m quest_workshop.compare --llm fake
  python -m quest_workshop.compare --llm gemini            # 실제 모델로. 결과는 output/compare/
  python -m quest_workshop.compare --llm gemini --repeat 3 # 3번씩 돌려 평균
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from .checks import code_check
from .graph import build_graph
from .llm import make_llm
from .lore import load_cards, load_main_story, retrieve
from .run import run_one

# (요청, 기대: ship = 설정 문제 없이 내보내야 함 / stop = 멈추거나 질문해야 함, 설명)
REQUESTS = [
    ("이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트", "ship", "평범한 요청"),
    ("이장이 바닷가에서 떠내려온 갑옷을 찾아 달라는 퀘스트", "ship", "요청이 설정과 충돌(솔뫼에 바다 없음) → 바꿔 써야 함"),
    ("도사가 기계 동료와 함께 기사를 구하는 퀘스트", "stop", "뒷장 요소(기계는 2장)"),
    ("주모가 빨래터에서 그늘이 커지는 괴물 소문을 조사해 달라는 퀘스트", "ship", "새 카드로 만든 요청"),
    ("뒷산 바위 제단의 유래를 알아보는 퀘스트", "stop", "설정에 없는 내용(지어내면 안 됨)"),
]
CONFIGS = {"A": "작성자만", "B": "설정 검수 + 재작성", "C": "두 검수자 + 판정"}


class CountingLLM:
    """모델 호출 수를 센다."""

    def __init__(self, llm):
        self.llm, self.calls = llm, 0
        self.name = llm.name

    def __getattr__(self, name):
        fn = getattr(self.llm, name)

        def wrapped(*a, **kw):
            self.calls += 1
            return fn(*a, **kw)
        return wrapped


def run_a(llm, cards, request, chapter, beats) -> dict:
    hits = retrieve(request, cards, chapter)
    guide = {"main_beats": [{"beat_id": b["beat_id"], "title": b["title"], "summary": b["summary"]} for b in beats],
             "rules": [], "human_feedback": ""}
    draft = llm.write(request, [c.for_writer() for c in hits], chapter, [], None, guide=guide)
    # 작성자 혼자서도 '설정에 없다'고 질문할 수는 있다. 그때는 멈춘 것으로 쳐 준다(A에 불리하지 않게)
    return {"stop_reason": "lore_gap" if draft.get("questions") else "shipped", "draft": draft, "plan_notes": 0}


def run_graph(llm, cards, request, chapter, with_plan: bool) -> dict:
    with tempfile.TemporaryDirectory() as d:     # 비교 실행은 실제 승인 목록에 남기지 않는다
        app = build_graph(llm, cards, save_dir=d, with_plan_review=with_plan)
        r = run_one(app, request, [{"approve": True}], chapter=chapter, quiet=True)
    stop = r.get("stop_reason")
    return {"stop_reason": "shipped" if stop == "approved" else stop, "draft": r.get("draft", {}),
            "plan_notes": len(r.get("plan_review", {}).get("notes", []))}


def audit(llm, cards, out: dict, chapter, beats) -> dict:
    """내보낸 초안만 점검한다. 멈춘 것은 내보낸 게 없으니 문제 0."""
    if out["stop_reason"] != "shipped":
        return {"code": [], "ai": []}
    code = code_check(out["draft"], cards, chapter, beats)
    gaps = out["draft"].get("questions", [])
    rv = llm.review(out["draft"], [c.for_reviewer() for c in cards.values()], chapter)
    ai = rv.get("issues", []) if rv.get("verdict") in ("충돌", "설정에 없음") else []
    return {"code": code, "ai": ai, "unasked_gaps": gaps}


def compare(llm, repeat: int = 1, chapter: int = 1, on_log=print) -> dict:
    cards = load_cards()
    beats = [b for b in load_main_story() if b.get("chapter") == chapter]
    rows = []
    for rnd in range(repeat):
        for req, expect, why in REQUESTS:
            for key in CONFIGS:
                counter = CountingLLM(llm)
                t0 = time.time()
                try:
                    if key == "A":
                        out = run_a(counter, cards, req, chapter, beats)
                    else:
                        out = run_graph(counter, cards, req, chapter, with_plan=(key == "C"))
                    err = ""
                except Exception as e:  # 한 칸이 실패해도 표는 끝까지 만든다
                    out, err = {"stop_reason": "error", "draft": {}, "plan_notes": 0}, str(e)[:200]
                secs, calls = time.time() - t0, counter.calls
                a = audit(llm, cards, out, chapter, beats) if not err else {"code": [], "ai": []}
                shipped = out["stop_reason"] == "shipped"
                ok = (shipped and not a["code"] and not a["ai"]) if expect == "ship" else (not shipped and not err)
                row = {"round": rnd + 1, "request": req, "expect": expect, "config": key,
                       "stop_reason": out["stop_reason"], "ok": ok, "code_issues": len(a["code"]),
                       "ai_issues": len(a["ai"]), "plan_notes": out["plan_notes"], "calls": calls,
                       "secs": round(secs, 1), "title": out["draft"].get("title", ""), "error": err,
                       "issue_detail": [i.get("detail", "") for i in a["code"]]
                                       + [i.get("problem", "") for i in a["ai"]]}
                rows.append(row)
                on_log(f"[{key}] {req[:24]}… → {row['stop_reason']} {'✓' if ok else '✗'} "
                       f"(코드 {row['code_issues']}, AI {row['ai_issues']}, 호출 {calls}, {row['secs']}초)")
    return {"model": llm.name, "repeat": repeat, "rows": rows}


def summary(result: dict) -> dict:
    out = {}
    for key in CONFIGS:
        rs = [r for r in result["rows"] if r["config"] == key]
        n = max(result["repeat"], 1)
        out[key] = {"ok": sum(r["ok"] for r in rs) / n, "total": len(REQUESTS),
                    "code": sum(r["code_issues"] for r in rs) / n, "ai": sum(r["ai_issues"] for r in rs) / n,
                    "plan": sum(r["plan_notes"] for r in rs) / n, "calls": sum(r["calls"] for r in rs) / n,
                    "secs": sum(r["secs"] for r in rs) / n,
                    "errors": sum(bool(r["error"]) for r in rs)}
    return out


def to_markdown(result: dict) -> str:
    s = summary(result)
    f = lambda x: f"{x:g}" if isinstance(x, (int, float)) else str(x)
    lines = [f"# A/B/C 비교 ({result['model']}, {result['repeat']}회 평균)", "",
             "| 구성 | 기대대로 끝남 | 내보낸 초안의 설정 문제(코드) | AI 점검(참고) | 기획 의견 | 모델 호출 | 걸린 시간(초) |",
             "|---|---|---|---|---|---|---|"]
    for k, name in CONFIGS.items():
        v = s[k]
        lines.append(f"| {k} {name} | {f(v['ok'])}/{v['total']} | {f(v['code'])} | {f(v['ai'])} | {f(v['plan'])} "
                     f"| {f(v['calls'])} | {f(round(v['secs'], 1))} |")
    lines += ["", "## 요청별", "", "| 요청 | 기대 | A | B | C |", "|---|---|---|---|---|"]
    for req, expect, why in REQUESTS:
        cells = []
        for k in CONFIGS:
            rs = [r for r in result["rows"] if r["request"] == req and r["config"] == k]
            cells.append(" / ".join(f"{'✓' if r['ok'] else '✗'} {r['stop_reason']}" for r in rs))
        lines.append(f"| {req} ({why}) | {'내보냄' if expect == 'ship' else '멈춤'} | " + " | ".join(cells) + " |")
    bad = [r for r in result["rows"] if r["issue_detail"] or r["error"]]
    if bad:
        lines += ["", "## 걸린 문제", ""]
        for r in bad:
            for d in r["issue_detail"] + ([f"오류: {r['error']}"] if r["error"] else []):
                lines.append(f"- {r['config']} · {r['request'][:30]}… · {d}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="A/B/C 비교")
    p.add_argument("--llm", default="auto", choices=["auto", "fake", "gemini"])
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--out", default="output/compare")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    llm = make_llm(a.llm)
    print(f"모델: {llm.name}, 요청 {len(REQUESTS)}개 × 구성 3개 × {a.repeat}회")
    result = compare(llm, a.repeat)
    md = to_markdown(result)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out / f"compare_{llm.name}_{stamp}.md").write_text(md, encoding="utf-8")
    (out / f"compare_{llm.name}_{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
    print("\n" + md)
    print(f"저장: {out}/compare_{llm.name}_{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
