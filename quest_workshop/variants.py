"""반복 퀘스트 재사용: 승인된 퀘스트 하나에서 의뢰인·대사만 바꾼 변형을 만든다.

게임 데이터로 보면 단계(진행 조건)·보상·열림 조건은 그대로 재사용하고, 의뢰 NPC와 대사만 바뀐 행이 늘어난다.

변형마다 점검
  - 설정 검수: 코드 검사 + AI 설정 검수(작업실과 같은 기준)
  - 골격 유지: 단계 수·순서가 같고, 의뢰인 카드 말고는 근거 카드가 같은가(데이터 재사용이 되는가)
  - 대사 겹침: 원본·다른 변형과 대사가 거의 같은가(이름만 바꾼 복사면 유저가 금방 알아챈다)
  - 거슬리지 않는가: 사람(기획자)이 나란히 읽고 판단한다

  python -m quest_workshop.variants output/quests/quest_xxx.json --llm gemini
  python -m quest_workshop.variants output/quests/quest_xxx.json --givers char_jumo_01,char_farmer_01,char_peddler_01
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

from .checks import code_check
from .lore import Card, load_cards, load_main_story
from .propose import similarity

NOT_GIVERS = {"char_dosa_01", "char_knight_01", "char_officials_01"}  # 주인공, 1장엔 말이 안 통하는 기사, 기관
SAME_LINES = 0.6   # 대사 전체가 이만큼 겹치면 '이름만 바꾼 복사'로 본다


def giver_candidates(cards: dict[str, Card], chapter: int, exclude: str = "") -> list[Card]:
    return [c for c in cards.values() if c.type == "인물" and chapter in c.chapters
            and c.card_id not in NOT_GIVERS and c.card_id != exclude]


def find_giver_id(draft: dict, cards: dict[str, Card]) -> str:
    name = draft.get("giver", "")
    for c in cards.values():
        if c.type == "인물" and name and (name == c.title or name in c.title or c.title in name):
            return c.card_id
    return ""


def _lines(d: dict) -> str:
    return " ".join(l.get("line", "") for l in d.get("dialogue", []))


def skeleton_issues(base: dict, var: dict, old_id: str, new_id: str) -> list[str]:
    out = []
    bs, vs = base.get("steps", []), var.get("steps", [])
    if len(bs) != len(vs):
        out.append(f"단계 수가 다름: 원본 {len(bs)}개, 변형 {len(vs)}개")
        return out
    for b, v in zip(bs, vs):
        want = {new_id if r == old_id else r for r in b.get("lore_refs", [])}
        got = set(v.get("lore_refs", []))
        if (want - {new_id}) != (got - {new_id}):
            out.append(f"{b.get('order')}단계 근거 카드가 바뀜: {sorted(want)} → {sorted(got)}")
    return out


def make_variants(llm, record: dict, giver_ids: list[str] | None = None, n: int = 3,
                  cards: dict[str, Card] | None = None, on_log=print) -> dict:
    cards = cards or load_cards()
    base = record["draft"]
    chapter = record.get("chapter", 1)
    beats = [b for b in load_main_story() if b.get("chapter") == chapter]
    guide = {"main_beats": [{"beat_id": b["beat_id"], "title": b["title"], "summary": b["summary"]} for b in beats]}
    old_id = find_giver_id(base, cards)
    pool = giver_candidates(cards, chapter, exclude=old_id)
    chosen = [cards[i] for i in giver_ids if i in cards] if giver_ids else pool[:n]
    out = []
    for g in chosen:
        others = [{"title": v["draft"]["title"], "dialogue": v["draft"]["dialogue"]} for v in out]
        try:
            raw = llm.variant(base, g.for_writer(), chapter, others, guide=guide)
        except Exception as e:  # 하나가 실패해도 나머지는 만든다
            on_log(f"[변형] {g.title}: 실패 {str(e)[:120]}")
            continue
        d = copy.deepcopy(base)
        d.update({k: raw[k] for k in ("title", "summary", "giver", "steps", "dialogue") if k in raw})
        d["motive"] = raw.get("motive", "")
        d["questions"], d["request_changes"] = [], []
        code = code_check(d, cards, chapter, beats)
        rv = llm.review(d, [c.for_reviewer() for c in cards.values()], chapter, guide=guide)
        skel = skeleton_issues(base, d, old_id, g.card_id)
        out.append({"giver_card_id": g.card_id, "draft": d, "code_issues": code, "review": rv,
                    "skeleton_issues": skel})
    # 대사 겹침: 원본과, 변형끼리
    for i, v in enumerate(out):
        sims = [similarity(_lines(v["draft"]), _lines(base))] + \
               [similarity(_lines(v["draft"]), _lines(w["draft"])) for j, w in enumerate(out) if j != i]
        v["max_line_similarity"] = round(max(sims), 2)
        v["copied_lines"] = v["max_line_similarity"] >= SAME_LINES
        v["lore_ok"] = not v["code_issues"] and v["review"].get("verdict") == "문제없음"
        on_log(f"[변형] {v['draft']['giver']}: 설정 {'통과' if v['lore_ok'] else '문제'}, "
               f"골격 {'유지' if not v['skeleton_issues'] else '바뀜'}, 대사 겹침 {v['max_line_similarity']}")
    return {"base_giver_card_id": old_id, "base": base, "request": record.get("request", ""), "variants": out}


def to_markdown(result: dict) -> str:
    def block(d: dict) -> list[str]:
        ls = [f"**{d.get('title', '')}** · 의뢰인: {d.get('giver', '')}"]
        if d.get("motive"):
            ls.append(f"- 동기: {d['motive']}")
        ls += [f"- {s.get('order')}. {s.get('text')}" for s in d.get("steps", [])]
        ls += [f"> **{l.get('speaker')}**: {l.get('line')}" for l in d.get("dialogue", [])]
        return ls
    md = [f"# 반복 퀘스트 변형: {result['base'].get('title', '')}", "", "## 원본", ""] + block(result["base"])
    md += ["", "## 점검", "", "| 의뢰인 | 설정 검수 | 골격 유지 | 대사 겹침(0~1) |", "|---|---|---|---|"]
    for v in result["variants"]:
        md.append(f"| {v['draft']['giver']} | {'통과' if v['lore_ok'] else '문제'} | "
                  f"{'유지' if not v['skeleton_issues'] else '바뀜'} | {v['max_line_similarity']}"
                  f"{' (복사 의심)' if v['copied_lines'] else ''} |")
    for v in result["variants"]:
        md += ["", f"## 변형: {v['draft']['giver']}", ""] + block(v["draft"])
        probs = [i.get("detail", "") for i in v["code_issues"]] + \
                [i.get("problem", "") for i in v["review"].get("issues", [])] + v["skeleton_issues"]
        md += [f"- ⚠ {p}" for p in probs if p]
    md += ["", "## 사람 판정(기획자)", "", "- [ ] 원본과 변형 3개를 이어 읽었을 때 거슬리지 않는다"]
    return "\n".join(md) + "\n"


def main(argv=None) -> int:
    from .llm import make_llm
    p = argparse.ArgumentParser(description="반복 퀘스트 변형")
    p.add_argument("quest", help="승인된 퀘스트 JSON 파일")
    p.add_argument("--llm", default="auto", choices=["auto", "fake", "gemini"])
    p.add_argument("--givers", default="", help="새 의뢰인 카드 번호, 쉼표로. 비우면 앞에서 3명")
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--out", default="output/variants")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    record = json.loads(Path(a.quest).read_text(encoding="utf-8"))
    result = make_variants(make_llm(a.llm), record, [g for g in a.givers.split(",") if g] or None, a.n)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out / f"variants_{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(result)
    (out / f"variants_{stamp}.md").write_text(md, encoding="utf-8")
    print("\n" + md + f"\n저장: {out}/variants_{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
