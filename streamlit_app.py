"""천지인 퀘스트 작업실 웹 화면.

    streamlit run streamlit_app.py

- 오늘의 제안 받기: 제안 에이전트가 요청 N개 → 각각 작성·설정 검수·기획 검수 → 카드로 승인/고칠 점/거절
- 요청 직접 넣기: 기획자가 요청 한 줄을 넣어 같은 흐름으로
- 승인된 퀘스트: 저장된 퀘스트 보기, JSON 내려받기

환경 변수(.env 또는 Streamlit Cloud Secrets)
  GOOGLE_API_KEY   있으면 Gemini, 없으면 가짜 모델
  QUEST_LLM        auto(기본) / fake / gemini
  QUEST_SAVE_DIR   승인한 퀘스트를 저장할 폴더(기본 output/quests)
  APP_PASSWORD     정해 두면 이 암호를 넣어야 화면이 열린다(공개 배포 때 키 남용 방지).
                   암호 없이는 "둘러보기"만 된다: 가짜 모델, 이 창에서만 쓰는 임시 폴더, GitHub 저장 안 함.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from quest_workshop.graph import build_graph
from quest_workshop.llm import make_llm
from quest_workshop.guide import add_discard, add_rule, load_rules, remove_rule
from quest_workshop.lore import load_cards, load_main_story, unlock_text
from quest_workshop.propose import answer, build_batch_graph
from quest_workshop.sync import local_only, pull_all
from quest_workshop.variants import find_giver_id, giver_candidates, make_variants

st.set_page_config(page_title="천지인 퀘스트 작업실", page_icon="🖌️", layout="wide",
                   initial_sidebar_state="collapsed")

STATUS_LABEL = {
    "승인 대기": ("승인 대기", "cj-wait"),
    "approved": ("승인됨", "cj-ok"),
    "rejected": ("거절됨", "cj-no"),
    "needs_review": ("사람 검토 필요", "cj-warn"),
    "lore_gap": ("설정 구멍", "cj-gap"),
}


def pill(status) -> str:
    text, cls = STATUS_LABEL.get(status, (str(status), "cj-no"))
    return f'<span class="cj-pill {cls}">{text}</span>'


# ---------------------------------------------------------------- 준비

def access_mode() -> str | None:
    """"full"(암호를 넣었거나 암호가 없음) / "browse"(둘러보기) / None(아직 고르지 않음)."""
    pw = os.getenv("APP_PASSWORD")
    if not pw or st.session_state.get("pw_ok"):
        return "full"
    if st.session_state.get("browse"):
        return "browse"
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**둘러보기** · 암호 없이")
        st.caption("가짜 모델로 제안 → 작성 → 두 검수 → 승인 흐름을 직접 눌러 볼 수 있어요. "
                   "API를 부르지 않고, 결과는 이 창에서만 쓰고 사라져요. "
                   "'승인된 퀘스트' 탭에는 실제 Gemini로 만든 퀘스트가 있어요.")
        if st.button("둘러보기 시작", type="primary"):
            st.session_state.browse = True
            st.rerun()
    with right.container(border=True):
        st.markdown("**실제 모델(Gemini)** · 암호 필요")
        typed = st.text_input("암호", type="password")
        if typed and typed == pw:
            st.session_state.pw_ok = True
            st.rerun()
        elif typed:
            st.error("암호가 맞지 않아요.")
    return None


@st.cache_resource
def pulled_once(save_dir: str) -> str:
    """둘러보기도 실제 승인 퀘스트를 보여 주도록, 서버당 한 번만 GitHub에서 받아 온다."""
    return pull_all(save_dir)


def browse_dir(real_dir: str) -> str:
    """둘러보기 손님마다 임시 폴더. 실제 저장 폴더의 퀘스트·지침을 복사해 두고, GitHub에는 올리지 않는다."""
    if "browse_dir" not in st.session_state:
        pulled_once(real_dir)
        d = Path(tempfile.mkdtemp(prefix="browse_"))
        src = Path(real_dir)
        if src.is_dir():
            for f in src.glob("*.json"):
                shutil.copy(f, d / f.name)
        local_only(d)
        st.session_state.browse_dir = str(d)
    return st.session_state.browse_dir


@st.cache_resource(max_entries=30)
def workshop(kind: str, save_dir: str):
    """모델·그래프를 한 번만 만든다. 승인 대기 중인 퀘스트(체크포인트)가 화면을 다시 그려도 유지된다."""
    pulled = pull_all(save_dir)   # 인터넷 버전: 재시작 뒤 GitHub에 저장해 둔 퀘스트·지침을 다시 받아 온다
    if pulled:
        print(pulled)
    llm = make_llm(kind)
    cards = load_cards()
    app = build_graph(llm, cards, save_dir=save_dir)
    batch = build_batch_graph(llm, app, cards, save_dir=save_dir)
    return llm, app, batch


def item_from_result(result: dict, base: dict) -> dict:
    """그래프 결과를 카드 하나의 상태로 바꾼다."""
    item = dict(base)
    item["log"] = result.get("log", item.get("log", []))
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        item.update(status="승인 대기", draft=payload.get("draft", {}),
                    plan_review=payload.get("plan_review", {}),
                    request_notes=payload.get("request_notes", []))
    else:
        item.update(status=result.get("stop_reason"), draft=result.get("draft", item.get("draft", {})),
                    plan_review=result.get("plan_review", item.get("plan_review", {})),
                    lore_gaps=result.get("lore_gaps", []), saved_path=result.get("saved_path", ""))
    return item


# ---------------------------------------------------------------- 반복 퀘스트 변형

def render_variant_tool(path: Path, rec: dict) -> None:
    """승인된 퀘스트 하나에서 의뢰인·대사만 바꾼 변형을 만들어 나란히 보여 준다."""
    cards = load_cards()
    pool = giver_candidates(cards, rec.get("chapter", 1), exclude=find_giver_id(rec.get("draft", {}), cards))
    names = {c.card_id: c.title for c in pool}
    st.markdown("**반복 퀘스트로 재사용** · 단계·보상·열림 조건은 그대로, 의뢰인과 대사만 바꿔요.")
    picked = st.multiselect("새 의뢰인", list(names), default=list(names)[:3], format_func=names.get,
                            key=f"vg_{path.name}", max_selections=5)
    key = f"var_{path.name}"
    if st.button("변형 만들기", key=f"vb_{path.name}", disabled=not picked):
        with st.spinner("변형 쓰고 설정 검수하는 중…"):
            st.session_state[key] = make_variants(llm, rec, picked, on_log=lambda *_: None)
    res = st.session_state.get(key)
    if not res:
        return
    for e in res.get("errors", []):
        st.error(f"{e['giver']} 변형 실패 · {e['error']}")
    if not res["variants"]:
        st.warning("변형을 하나도 만들지 못했어요. 위 오류를 확인하고 다시 눌러 주세요.")
        return
    cols = st.columns(len(res["variants"]) + 1)
    with cols[0]:
        st.caption("원본")
        render_draft(res["base"])
    for col, v in zip(cols[1:], res["variants"]):
        with col:
            badges = [("설정 통과" if v["lore_ok"] else "설정 문제"),
                      ("골격 유지" if not v["skeleton_issues"] else "골격 바뀜"),
                      f"대사 겹침 {v['max_line_similarity']}" + (" (복사 의심)" if v["copied_lines"] else "")]
            st.caption(" · ".join(badges))
            if v["draft"].get("motive"):
                st.markdown(f"**동기** · {v['draft']['motive']}")
            render_draft(v["draft"])
            for prob in ([i.get("detail", "") for i in v["code_issues"]]
                         + [i.get("problem", "") for i in v["review"].get("issues", [])] + v["skeleton_issues"]):
                if prob:
                    st.warning(prob)
    st.download_button("변형 JSON 내려받기", json.dumps(res, ensure_ascii=False, indent=2),
                       file_name=f"variants_{path.stem}.json", mime="application/json", key=f"vdl_{path.name}")


# ---------------------------------------------------------------- 그리기

def render_draft(d: dict, head: bool = True) -> None:
    if head:
        st.markdown(f"**{d.get('title', '(제목 없음)')}** · 의뢰인: {d.get('giver', '-')}")
    if d.get("summary"):
        st.caption(d["summary"])
    if d.get("intent"):
        st.markdown(f"**의도** · {d['intent']}")
    if unlock_text(d.get("unlock")):
        st.markdown(f"**열림 조건** · {unlock_text(d.get('unlock'))}")
    for s in d.get("steps", []):
        st.markdown(f"{s.get('order')}. {s.get('text')}  `{', '.join(s.get('lore_refs', []))}`")
    if d.get("dialogue"):
        lines = []
        for l in d["dialogue"]:
            direction = f" *({l['direction']})*" if l.get("direction") else ""
            lines.append(f"> **{l.get('speaker')}**: {l.get('line')}{direction}")
        st.markdown("  \n".join(lines))
    reward = d.get("reward", {}).get("text")
    if reward:
        st.markdown(f"보상(가안): {reward}")
    for c in d.get("request_changes", []):
        st.caption(f"요청에서 바꾼 점: {c}")


def render_plan(pr: dict) -> None:
    if not pr:
        return
    st.markdown(f"**기획 검수** · {pr.get('verdict')}")
    if pr.get("good"):
        st.markdown("잘된 점\n" + "\n".join(f"- {g}" for g in pr["good"]))
    if pr.get("notes"):
        st.markdown("고칠 점\n" + "\n".join(f"- {n['point']}  \n  → {n['fix']}" for n in pr["notes"]))


def replace_with_new(key: str, item: dict, reason: str = "") -> None:
    """요청 자체가 틀렸을 때: 이 요청을 버리고(다시 제안하지 않게 기억) 제안자에게 새 요청 하나를 받는다."""
    if item.get("status") == "승인 대기":
        answer(app, item["thread_id"], {"approve": False})
    add_discard(save_dir, item["request"], reason)
    del st.session_state["items"][key]
    pending = [v["request"] for v in st.session_state["items"].values() if v.get("status") != "approved"]
    out = batch.invoke({"chapter": chapter, "n": 1, "pending": pending}, {"max_concurrency": 1})
    for r in out.get("results", []):
        st.session_state["items"][f"p_{uuid.uuid4().hex[:8]}"] = {**r, "source": "제안"}
    if out.get("needed_lore"):
        st.session_state["needed_lore"] = out["needed_lore"]


def render_item(key: str, item: dict, app) -> None:
    with st.container(border=True):
        top = st.columns([5, 1])
        d = item.get("draft", {})
        top[0].markdown(f"#### {d.get('title') or item['request']}")
        top[0].caption(f"요청: {item['request']}" + (f"  ·  의뢰인: {d['giver']}" if d.get("giver") else ""))
        top[1].markdown(f'<div style="text-align:right">{pill(item.get("status"))}</div>', unsafe_allow_html=True)
        if item.get("why"):
            st.caption(f"제안 이유: {item['why']}")
        for n in item.get("request_notes", []):
            st.warning(f"요청 충돌 → 설정에 맞게 바꿈: {n}")

        left, right = st.columns([3, 2])
        with left:
            render_draft(item.get("draft", {}), head=False)
        with right:
            render_plan(item.get("plan_review", {}))
            for g in item.get("lore_gaps", []):
                st.info(f"설정 구멍: {g}")

        with st.expander("진행 로그"):
            st.code("\n".join(item.get("log", [])), language=None)

        if item.get("status") == "승인 대기":
            fb = st.text_area("고칠 점 (적고 '고쳐 쓰기'를 누르면 반영해서 다시 써요. 이 의견은 '기획 지침'에 남아 다음 퀘스트에도 반영돼요)", key=f"fb_{key}")
            b = st.columns(4)
            ans = None
            if b[0].button("승인", key=f"ok_{key}", type="primary"):
                ans = {"approve": True}
            if b[1].button("고쳐 쓰기", key=f"re_{key}", disabled=not fb.strip()):
                ans = {"approve": False, "feedback": fb.strip()}
            if b[2].button("거절", key=f"no_{key}"):
                ans = {"approve": False}
            if item.get("source") == "제안" and b[3].button("버리고 새 제안", key=f"new_{key}",
                                                         help="요청 자체가 틀렸을 때. 이 요청은 다시 제안하지 않아요"):
                with st.spinner("새 요청을 제안받아 쓰는 중…"):
                    replace_with_new(key, item, fb)
                st.rerun()
            if ans is not None:
                with st.spinner("반영하는 중…"):
                    res = answer(app, item["thread_id"], ans)
                st.session_state["items"][key] = item_from_result(res, item)
                st.rerun()
        elif item.get("saved_path"):
            st.caption(f"저장: {item['saved_path']}")
        elif item.get("source") == "제안":
            if st.button("버리고 새 제안", key=f"new_{key}", help="이 요청은 다시 제안하지 않아요"):
                with st.spinner("새 요청을 제안받아 쓰는 중…"):
                    replace_with_new(key, item)
                st.rerun()


# ---------------------------------------------------------------- 화면

CSS = """
<style>
header[data-testid="stHeader"] {display: none;}
.block-container {padding-top: 2rem; max-width: 1180px;}
.cj-head {display:flex; align-items:baseline; gap:.75rem; margin-bottom:.25rem;}
.cj-head h1 {margin:0; padding:0; font-size:2rem;}
.cj-sub {color:#6B655C; margin-bottom:1.25rem;}
.cj-pill {display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.8rem;
          font-weight:500; white-space:nowrap;}
.cj-wait {background:#F3E3B5; color:#6A4E00;} .cj-ok {background:#D5E8D4; color:#24502A;}
.cj-no {background:#E4E0D8; color:#5A554C;} .cj-warn {background:#F2D0CB; color:#7A2418;}
.cj-gap {background:#D3DFEC; color:#22405E;}
.cj-meta {color:#6B655C; font-size:.85rem;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
st.markdown('<div class="cj-head"><h1>천지인 퀘스트 작업실</h1></div>'
            '<div class="cj-sub">설정 카드를 근거로 1장 퀘스트를 제안하고 쓰면, 설정·기획 두 검수자가 보고 기획자가 결재합니다.</div>',
            unsafe_allow_html=True)

mode = access_mode()
if mode is None:
    st.stop()

has_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
default_kind = os.getenv("QUEST_LLM", "auto")
kinds = ["auto", "fake", "gemini"]
save_dir = os.getenv("QUEST_SAVE_DIR", "output/quests")
if mode == "browse":
    save_dir = browse_dir(save_dir)
    default_kind, kinds = "fake", ["fake"]
    st.info("둘러보기 모드예요. 가짜 모델이라 정해진 답이 나오고, 흐름(제안 → 작성 → 설정·기획 검수 → 승인)을 "
            "확인하는 용도예요. 여기서 승인한 것은 이 창을 닫으면 사라져요.")

with st.container(border=True):
    c = st.columns([1.2, 0.8, 1.4, 1.4])
    kind = c[0].selectbox("모델", kinds, index=kinds.index(default_kind) if default_kind in kinds else 0,
                          help="auto: 키가 있으면 Gemini, 없으면 가짜 모델")
    chapter = c[1].number_input("장", min_value=1, max_value=3, value=1)
    n = c[2].slider("오늘 제안 받을 개수", 1, 5, 5)
    parallel = c[3].slider("동시에 작성·검수", 1, 5, 2, help="무료 키는 분당 호출 한도가 있어 작게 둔다")
if kind == "gemini" and not has_key:
    st.error("GOOGLE_API_KEY가 없어요. 모델을 fake로 바꾸거나 키를 넣어 주세요.")
    st.stop()

llm, app, batch = workshop(kind, save_dir)
st.markdown(f'<div class="cj-meta">지금 모델 <b>{llm.name}</b> · 저장 폴더 <code>{save_dir}</code></div>',
            unsafe_allow_html=True)

st.session_state.setdefault("items", {})
st.session_state.setdefault("needed_lore", [])

tab_propose, tab_request, tab_saved, tab_guide = st.tabs(["오늘의 제안", "요청 직접 넣기", "승인된 퀘스트", "기획 지침"])

with tab_propose:
    if st.button(f"오늘의 제안 받기 ({n}개)", type="primary"):
        with st.status("제안 받는 중… 몇 분 걸릴 수 있어요", expanded=True) as box:
            st.session_state["needed_lore"] = []
            pending = [v["request"] for v in st.session_state["items"].values() if v.get("status") == "승인 대기"]
            for chunk in batch.stream({"chapter": chapter, "n": n, "pending": pending}, {"max_concurrency": parallel},
                                      stream_mode="updates"):
                for node, update in chunk.items():
                    if not isinstance(update, dict):
                        continue
                    for line in update.get("log", []):
                        st.write(line)
                    if update.get("needed_lore"):
                        st.session_state["needed_lore"] = update["needed_lore"]
                    for r in update.get("results", []):
                        key = f"p_{uuid.uuid4().hex[:8]}"
                        st.session_state["items"][key] = {**r, "source": "제안"}
                        st.write(f"✓ {r['request']} → {r['status']}")
            box.update(label="제안 완료", state="complete", expanded=False)

    if st.session_state["needed_lore"]:
        st.warning("새 소재가 모자라요. 이런 설정을 추가하면 더 만들 수 있어요:\n\n"
                   + "\n".join(f"- {x}" for x in st.session_state["needed_lore"]))

    items = {k: v for k, v in st.session_state["items"].items() if v.get("source") == "제안"}
    if not items:
        st.info("버튼을 누르면 AI가 1장 설정과 이미 승인한 퀘스트를 보고 겹치지 않는 퀘스트 요청을 제안해요.")
    for key, item in reversed(list(items.items())):
        render_item(key, item, app)

with tab_request:
    req = st.text_input("요청 한 줄", placeholder="예: 이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트")
    if st.button("작성·검수 시작", disabled=not req.strip()):
        tid = str(uuid.uuid4())
        with st.status("작성·검수 중…", expanded=True) as box:
            cfg = {"configurable": {"thread_id": tid}}
            for chunk in app.stream({"request": req.strip(), "chapter": chapter}, cfg, stream_mode="updates"):
                for node, update in chunk.items():
                    if isinstance(update, dict):
                        for line in update.get("log", []):
                            st.write(line)
            box.update(label="완료", state="complete", expanded=False)
        snap = app.get_state(cfg)
        result = dict(snap.values)
        interrupts = [i for t in snap.tasks for i in t.interrupts]
        if interrupts:
            result["__interrupt__"] = interrupts
        key = f"r_{uuid.uuid4().hex[:8]}"
        st.session_state["items"][key] = item_from_result(
            result, {"request": req.strip(), "thread_id": tid, "source": "직접"})
    for key, item in reversed([(k, v) for k, v in st.session_state["items"].items() if v.get("source") == "직접"]):
        render_item(key, item, app)

with tab_saved:
    files = sorted(Path(save_dir).glob("quest_*.json"), reverse=True)
    st.caption(f"{len(files)}개 · `{save_dir}`")
    for p in files:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d = rec.get("draft", {})
        with st.expander(f"{d.get('title', p.stem)} — {rec.get('request', '')}"):
            render_draft(d)
            render_plan(rec.get("plan_review", {}))
            st.download_button("JSON 내려받기", p.read_text(encoding="utf-8"), file_name=p.name,
                               mime="application/json", key=f"dl_{p.name}")
            render_variant_tool(p, rec)

with tab_guide:
    st.markdown("**메인 시나리오 사건** · 메인 퀘스트 몫이라 이 작업실(일일·서브 퀘스트)에서는 쓰지 않아요. "
                "고치려면 `data/main_story_ch1.json`을 고쳐요.")
    for b in load_main_story(chapter):
        mark = "확정" if b.get("confirmed") else "가안"
        terms = f" · 금지어: {', '.join(b['block_terms'])}" if b.get("block_terms") else ""
        st.markdown(f"- **{b['title']}** ({mark}) — {b['summary']}{terms}")

    st.markdown("**기획자 지침** · 고쳐 쓰기·거절 때 적은 의견이 여기 쌓이고, 이후 모든 퀘스트의 제안·작성·기획 검수에 반영돼요. "
                "이 퀘스트에만 해당하는 의견은 지우세요.")
    rules = load_rules(save_dir)
    if not rules:
        st.caption("아직 없어요.")
    for r in rules:
        c = st.columns([8, 1])
        src = f"  \n<span class='cj-meta'>{r['added']} · {r['source']}</span>" if r.get("source") else ""
        c[0].markdown(f"- {r['text']}{src}", unsafe_allow_html=True)
        if c[1].button("지우기", key=f"rm_{r['id']}"):
            remove_rule(save_dir, r["id"])
            st.rerun()
    new_rule = st.text_input("지침 직접 추가", placeholder="예: 기사는 말이 통하기 전이므로 대사는 알아들을 수 없는 외침으로만 쓴다")
    if st.button("추가", disabled=not new_rule.strip()):
        add_rule(save_dir, new_rule, source="직접 추가")
        st.rerun()
