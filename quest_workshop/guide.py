"""기획자 지침: 기획자가 고쳐 쓰기·거절 때 준 의견을 기억해 두고, 다음 퀘스트(제안·작성·기획 검수)에 계속 반영한다.

게임 팀으로 치면 리드가 리뷰 때 한 말을 '기획 가이드 문서'에 한 줄씩 쌓아 두는 것.
퀘스트 하나에만 해당하는 의견도 쌓이므로, 기획자가 화면에서 지우거나 직접 추가할 수 있다.
저장 위치: 승인 퀘스트 폴더 안의 guidelines.json
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .sync import persist

MAX_IN_PROMPT = 15   # 프롬프트에 넣는 최근 지침 개수


def guide_path(save_dir: str | Path) -> Path:
    return Path(save_dir) / "guidelines.json"


def load_rules(save_dir: str | Path) -> list[dict]:
    p = guide_path(save_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _write(save_dir: str | Path, rules: list[dict]) -> None:
    p = guide_path(save_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    persist(p)


def add_rule(save_dir: str | Path, text: str, source: str = "") -> None:
    text = text.strip()
    rules = load_rules(save_dir)
    if not text or any(r["text"] == text for r in rules):
        return
    rules.append({"id": uuid.uuid4().hex[:8], "text": text, "source": source,
                  "added": time.strftime("%Y-%m-%d %H:%M")})
    _write(save_dir, rules)


def remove_rule(save_dir: str | Path, rule_id: str) -> None:
    _write(save_dir, [r for r in load_rules(save_dir) if r["id"] != rule_id])


def rule_texts(save_dir: str | Path) -> list[str]:
    return [r["text"] for r in load_rules(save_dir)][-MAX_IN_PROMPT:]


# ---------------------------------------------------------------- 버린 요청
# 기획자가 "이 요청 말고 새로 제안해 줘"로 버린 요청. 제안자가 같은(비슷한) 요청을 다시 내지 않게 기억한다.

def _discard_path(save_dir: str | Path) -> Path:
    return Path(save_dir) / "discarded.json"


def load_discards(save_dir: str | Path) -> list[dict]:
    try:
        return json.loads(_discard_path(save_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def add_discard(save_dir: str | Path, request: str, reason: str = "") -> None:
    items = load_discards(save_dir)
    if any(i["request"] == request for i in items):
        return
    items.append({"request": request, "reason": reason.strip(), "added": time.strftime("%Y-%m-%d %H:%M")})
    p = _discard_path(save_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    persist(p)
