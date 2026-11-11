"""작성자와 설정 검수자 역할을 하는 모델.

- FakeLLM: API 없이 정해진 규칙으로 답한다. 그래프 흐름을 확인하고 테스트하는 용도.
  가짜 모드의 설정 검수는 코드 검사(checks.py)만으로 이뤄지고, AI 의미 검수는 '문제없음'을 돌려준다.
- GeminiLLM: 실제 Gemini. GOOGLE_API_KEY(또는 GEMINI_API_KEY)를 .env에 넣으면 쓴다.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

from .schemas import Draft, PlanReview, Proposals, Review


class QuestLLM(Protocol):
    # guide: {"main_beats": [{title, summary}], "rules": [기획자 지침], "human_feedback": str}
    def write(self, request: str, cards: list[dict], chapter: int, feedback: list[str],
              previous: dict | None, guide: dict | None = None) -> dict: ...

    def review(self, draft: dict, cards: list[dict], chapter: int, guide: dict | None = None) -> dict: ...

    def plan_review(self, draft: dict, request: str, cards: list[dict], chapter: int,
                    guide: dict | None = None) -> dict:
        """{"verdict": "좋음"|"고칠 점", "good": [str], "notes": [{point, fix}]}"""
        ...

    def propose(self, cards: list[dict], chapter: int, existing: list[dict], n: int,
                guide: dict | None = None) -> dict:
        """{"items": [{request, why, card_ids}], "needed_lore": [str]}"""
        ...


# ---------------------------------------------------------------- 가짜 모델

# 설정에 맞는 대체어가 있는 것만 고쳐 쓸 수 있다. 대체어가 없으면(예: 기계 동료) 못 고친다.
SUBSTITUTE = {"바닷가": "뒷산 계곡", "바다": "계곡", "해변": "계곡", "항구": "장터"}

# 설정 카드에 '있다'까지만 적혀 있고 내력이 없는 것. 가짜 작성자는 이걸 지어내지 않고 질문으로 남긴다.
GAP_TRIGGERS = {
    "유래": "바위 제단을 누가, 언제, 왜 만들었는가? (place_mountain_01에는 '오래된 바위 제단이 있다'까지만 있다)",
}


# 가짜 제안자가 고르는 요청. 모두 1장 카드 안의 소재만 쓴다.
FAKE_PROPOSALS = [
    ("이장이 도사에게 고갯마루 서낭당에서 사라진 제물을 찾아 달라는 퀘스트", "place_shrine_01, event_ritual_01"),
    ("장터에 몰려든 담비굴 소인을 쫓아 달라는 이장의 퀘스트", "place_village_01, enemy_ch1_01"),
    ("뒷산 계곡에서 주운 낯선 쇳조각의 정체를 이장이 물어보는 퀘스트", "place_mountain_01, char_elder_01"),
    ("이장이 도사에게 어긋난 액막이제를 다시 지내 달라고 부탁하는 퀘스트", "event_ritual_01, char_elder_01"),
    ("관아 눈치를 보는 이장이 장터에 도는 괴소문을 잠재워 달라는 퀘스트", "char_elder_01, char_officials_01"),
    ("뒷산에서 길을 잃은 마을 사람을 찾아 달라는 이장의 퀘스트", "place_mountain_01, char_elder_01"),
    ("관아에서 내려온 주문서를 들고 서낭당 의식을 준비하는 퀘스트", "char_officials_01, place_shrine_01"),
]


class FakeLLM:
    name = "fake"

    def propose(self, cards, chapter, existing, n, guide=None):
        used = {e["request"] for e in existing} | {d.split(" (버린 이유")[0] for d in (guide or {}).get("discarded", [])}
        out = [{"request": r, "why": f"근거 카드: {ids}", "card_ids": ids.split(", ")}
               for r, ids in FAKE_PROPOSALS if r not in used][:n]
        needed = []
        if len(out) < n:
            least = sorted(cards, key=lambda c: c.get("used_in_quests", 0))[:2]
            needed = [f"가짜 제안자의 소재 {len(FAKE_PROPOSALS)}개를 다 썼다. "
                      f"덜 쓴 카드({', '.join(c['card_id'] for c in least)})를 넓히거나 새 인물·장소·사건 카드를 추가하면 더 만들 수 있다."]
        return {"items": out, "needed_lore": needed}

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        ids = {c["card_id"] for c in cards}
        req = request
        changes = []
        for fb in feedback:
            for bad, good in SUBSTITUTE.items():
                if bad in fb and bad in req:
                    req = req.replace(bad, good)
                    changes.append(f"{bad} → {good} (place_village_01)")

        giver_id = "char_elder_01" if "이장" in req and "char_elder_01" in ids else "char_dosa_01"
        giver = "솔뫼 이장" if giver_id == "char_elder_01" else "청운"
        place_id = next((i for i in ("place_mountain_01", "place_shrine_01", "place_village_01") if i in ids),
                        "char_dosa_01")
        place = {"place_mountain_01": "뒷산", "place_shrine_01": "고갯마루 서낭당",
                 "place_village_01": "솔뫼 장터"}.get(place_id, "솔뫼 근처")

        steps = [
            {"order": 1, "text": f"{giver}에게 부탁을 받는다: {req}", "lore_refs": [giver_id]},
            {"order": 2, "text": f"{place}로 가서 단서를 찾는다.", "lore_refs": [place_id]},
        ]
        if "enemy_ch1_01" in ids:
            steps.append({"order": 3, "text": "단서 근처에 몰려든 담비굴 소인을 먹선 매를 날려 쫓아낸다.",
                          "lore_refs": ["enemy_ch1_01", "char_dosa_01"]})

        scared = any("겁" in fb for fb in list(feedback) + list((guide or {}).get("rules", [])))
        elder_line = ("어, 어이쿠… 이거 큰일 났구먼. 도, 도사 양반, 제발 좀 봐 주시게…" if scared
                      else "어이쿠, 도사 양반. 이거 큰일 났구먼. 좀 봐 주시게.")
        dialogue = []
        if giver_id == "char_elder_01":
            dialogue.append({"speaker": "솔뫼 이장", "line": elder_line, "lore_refs": ["char_elder_01"]})
        dialogue.append({"speaker": "청운", "line": "알았어, 알았어. 대신 저녁은 이장님이 사.",
                         "lore_refs": ["char_dosa_01"]})

        reward_refs = ["item_talisman_01"] if "item_talisman_01" in ids else []
        reward = {"text": "엽전 한 꾸러미와 칙령 파자부 세 장" if reward_refs else "엽전 한 꾸러미",
                  "lore_refs": reward_refs}

        questions = [q for trig, q in GAP_TRIGGERS.items() if trig in req]
        intent = "마을의 작은 걱정을 풀어 주며 청운과 솔뫼 사람들 사이의 정을 느낀다."
        beat_ids = [b.get("beat_id", "") for b in (guide or {}).get("main_beats", [])]
        unlock = {"after": beat_ids[0] if beat_ids else "", "before": "",
                  "reason": "액막이제 뒤 마을에 괴이한 일이 생기기 시작한 때부터" if beat_ids else ""}
        return {"title": req[:30], "summary": req, "intent": intent, "giver": giver, "unlock": unlock, "steps": steps,
                "dialogue": dialogue, "reward": reward, "questions": questions,
                "request_changes": changes}

    def review(self, draft, cards, chapter, guide=None):
        return {"verdict": "문제없음", "issues": []}

    def plan_review(self, draft, request, cards, chapter, guide=None):
        notes = []
        if len(draft.get("steps", [])) < 3:
            notes.append({"point": f"진행 단계가 {len(draft.get('steps', []))}개뿐이라 목표까지 너무 짧다.",
                          "fix": "단서를 찾는 중간 단계(조사나 대화)를 하나 더 넣는다."})
        if not draft.get("intent", "").strip():
            notes.append({"point": "의도(플레이어가 느낄 감정)가 적혀 있지 않아 단계·대사가 무엇을 향하는지 판단하기 어렵다.",
                          "fix": "intent에 감정 목표를 한 줄로 적는다. 예: '낯선 이에 대한 경계가 호기심으로 바뀐다'"})
        if not any(d.get("speaker") == draft.get("giver") for d in draft.get("dialogue", [])):
            notes.append({"point": "의뢰인의 대사가 없어 부탁하는 동기가 안 보인다.",
                          "fix": "의뢰인이 왜 도사에게 부탁하는지 한 줄 넣는다."})
        return {"verdict": "고칠 점" if notes else "좋음", "good": ["청운의 능청스러운 말투가 드러난다."],
                "notes": notes}


# ---------------------------------------------------------------- Gemini

def guide_block(guide: dict | None) -> str:
    """메인 사건·기획자 지침·이 퀘스트에 준 기획자 의견을 프롬프트에 붙일 글로."""
    if not guide:
        return ""
    out = []
    if guide.get("main_beats"):
        out.append("메인 시나리오 사건(일어나는 순서대로. 메인 퀘스트 몫이라 서브 퀘스트에서 다시 하거나, 이름·장소만 바꿔 같은 흐름으로 쓰지 않는다)\n"
                   + "\n".join(f"- [{b.get('beat_id', '')}] {b['title']}: {b['summary']}" for b in guide["main_beats"]))
    if guide.get("rules"):
        out.append("기획자 지침(기획자가 이전 퀘스트들에 준 의견. 모든 퀘스트에 적용하고, 요청 문장보다 우선한다)\n"
                   + "\n".join(f"- {r}" for r in guide["rules"]))
    if guide.get("discarded"):
        out.append("기획자가 버린 요청(이것과 같거나 비슷한 요청은 다시 내지 않는다)\n"
                   + "\n".join(f"- {r}" for r in guide["discarded"]))
    if guide.get("human_feedback"):
        out.append(f"이 퀘스트에 기획자가 준 의견(가장 우선한다)\n- {guide['human_feedback']}")
    return "\n\n" + "\n\n".join(out) + "\n" if out else ""


WRITER_PROMPT = """너는 게임 퀘스트 기획자다. 아래 설정 카드만 근거로 {chapter}장 퀘스트 초안을 쓴다.

규칙
- 단계·대사·보상마다 근거가 된 카드 번호(card_id)를 lore_refs에 단다. 카드에 없는 번호는 쓰지 않는다.
- 카드에 없는 인물·장소·아이템·내력을 지어내지 않는다.
- 요청이 설정 카드와 부딪히면 카드를 따른다. 예: 카드에 없는 장소가 요청에 있으면 카드에 있는 비슷한 장소로 바꿔 쓴다.
  바꿨으면 몰래 넘어가지 말고 request_changes에 '무엇 → 무엇 (근거 card_id)'로 적는다.
- questions는 퀘스트의 뼈대(목표, 장소, 인물이나 장소의 내력)가 카드에 없어서 지어내지 않고는 쓸 수 없을 때만 남긴다.
  보상 종류·수량, 아이템을 누가 갖는지, 세부 연출처럼 기획자가 나중에 정해도 되는 것은 묻지 말고 무난한 가안으로 채운다
  (보상은 엽전·곡식 같은 평범한 것이면 카드 근거 없이 써도 된다). 대부분의 퀘스트에서 questions는 빈 목록이 정상이다.
- intent에는 이 퀘스트로 플레이어가 느끼게 할 감정을 한 줄로 적는다. 단계·대사는 그 감정을 향하게 쓴다.
- 인물 말투는 카드에 적힌 성격을 따른다. 분량은 단계 3~5개, 대사 2~6줄.
- dialogue의 line에는 실제로 하는 말만 쓴다. 행동·표정·연출은 direction에 따로 쓴다.
- 단계(steps)는 플레이어가 직접 하는 행동(이동·조사·대화·전투·아이템 사용)과 끝나는 조건이 보이게 쓴다.
- 이 퀘스트는 일일·서브 퀘스트다. 아래 메인 시나리오 사건은 쓰지 않는다. 요청이 메인 사건을 건드리면 인물·장소는 살리되
  사건과 목적을 다른 것으로 바꾸고 request_changes에 적는다.
- unlock에는 이 서브 퀘스트가 메인 시나리오의 어느 구간에서 열리는지 메인 사건 번호(beat_id)로 적는다.
  after는 이 사건이 끝난 뒤 열림, before는 이 사건 전까지만 가능. 내용과 맞아야 한다
  (예: 기사와 말이 안 통하는 장면이면 before는 귀밝이술 사건, 기사가 동료로 나오면 after는 보스전 사건).
- 기획자 지침과 기획자 의견은 요청 문장보다 우선한다. 기획자가 어떤 사건·소재를 쓰지 말라고 하면,
  같은 적·같은 동료 합류·같은 해결 방식처럼 흐름이 같은 것으로 이름만 바꿔 쓰지 말고 아예 다른 사건으로 바꾼다.
- 금지: 오니형 도깨비, 음양사 별 문양, 멸문·복수 서사.
{guide}
설정 카드
{cards}

요청
{request}
{feedback}"""

REVIEWER_PROMPT = """너는 게임 세계관 설정 검수자다. 아래 퀘스트 초안이 설정 카드와 맞는지 '사실'만 판단한다.
재미나 문장 품질은 판단하지 않는다.

보는 것
1. 초안 내용이 근거로 단 카드 내용과 실제로 맞는가(번호만 맞고 내용이 다르면 충돌).
2. 카드의 not_present에 적힌 것이 나오는가.
3. chapters에 {chapter}장이 없는 카드의 요소가 나오는가(뒷장 요소, 충돌).
4. spoiler에 적힌 내용을 초안이 미리 밝히는가(충돌).
5. 퀘스트를 판단하는 데 필요한 설정이 카드에 아예 없는가(설정에 없음).
6. 아래 메인 시나리오 사건을 다시 하거나, 이름만 바꿔 같은 흐름으로 쓰는가(충돌). 이 퀘스트는 일일·서브 퀘스트다.
7. 열림 조건(unlock)이 내용과 맞는가. 예: 기사와 말이 통하는 장면인데 귀밝이술 사건 전에 열리면 충돌.

verdict: 문제가 없으면 "문제없음", 하나라도 부딪히면 "충돌", 판단에 필요한 설정이 비어 있으면 "설정에 없음".

{guide}
설정 카드(검수자용, 스포일러 포함)
{cards}

퀘스트 초안
{draft}"""


PLAN_REVIEWER_PROMPT = """너는 게임 퀘스트 기획 리드다. 신입 기획자가 쓴 {chapter}장 퀘스트 초안을 '기획' 관점에서 리뷰한다.
설정이 맞는지는 다른 검수자가 본다. 너는 설정 사실을 판단하지 않는다.

보는 것
1. 목표: 플레이어가 무엇을 하면 끝나는지 분명한가.
2. 동기: 의뢰인이 왜 이 일을, 왜 도사에게 맡기는지 보이는가.
3. 진행: 단계가 자연스럽게 이어지고, 게임으로 만들 수 있는 행동(이동·조사·대화·전투)인가. 1장(20~40분 분량) 안의 작은 퀘스트로 알맞은 크기인가.
4. 인물: 대사가 카드에 적힌 성격·말투에 맞는가(특히 주인공 청운).
5. 요청 반영: 요청 문장의 의도를 살렸는가. 단 기획자 지침, 이 퀘스트에 준 기획자 의견, 메인 사건 금지가 요청보다 우선이다.
   그 때문에 요청에서 바뀐 부분은 지적하지 않고, 요청대로 되돌리라고 하지 않는다.
이 작업실의 퀘스트는 매일 만드는 짧은 일일·서브 퀘스트다. 메인 퀘스트만큼 깊은 감정선을 요구하지 않는다.
6. 의도: intent(플레이어가 느낄 감정)가 적혀 있는가. "이장 걱정을 덜어 준 소소한 뿌듯함"처럼 한 줄이면 충분하다.
   비어 있을 때만 무엇을 적으면 좋을지 제안하고, 깊이가 얕다는 이유로는 지적하지 않는다.
7. 단계의 목적(엄격히): 모든 단계가 목표나 의도에 보탬이 되는가. 반복해서 하는 퀘스트라 쓸데없는 단계는 매번 지루함이 된다.
   목적 없이 시간만 끄는 단계는 "몇 번 단계를 자르라"고 지적한다.
8. 행동과 주제(가볍게): 플레이어가 하는 행동이 퀘스트의 주제·의도와 크게 어긋날 때만 지적한다.
   예: '낯선 이와의 소통'이 주제인데 해결이 전투뿐이면 어긋난다.
9. 장르·테마 가드레일(엄격히): 한국 민속 판타지 톤과 세계 규칙 카드(rule_world_01, 예: 이 땅 사람은 다른 세계를 모른다)를 벗어나지 않는가.
   금지 클리셰(오니형 도깨비, 음양사 별 문양, 멸문·복수 서사)가 나오면 반드시 지적한다.
10. 메인 겹침·지침 위반(엄격히): 메인 시나리오 사건과 흐름이 비슷하거나 기획자 지침을 어기면 지적한다.
보상 종류와 수치는 보지 않는다(밸런스 담당 몫).

verdict: 바로 승인해도 될 만하면 "좋음", 고치면 확실히 나아질 점이 있으면 "고칠 점".
notes는 꼭 고칠 것만 0~3개, 중요한 것부터. 잘된 점(good)도 1~2개 적는다.

{guide}
인물·장소 카드(말투 참고용)
{cards}

요청
{request}

퀘스트 초안
{draft}"""


PROPOSER_PROMPT = """너는 게임 퀘스트 기획 리드다. 아래 설정 카드로 {chapter}장에 넣을 새 퀘스트 요청 {n}개를 제안한다.
요청은 기획자가 작성자에게 넘기는 한 줄 지시다. 예: "이장이 도사에게 뒷산의 이상한 빛을 조사해 달라고 맡기는 퀘스트"

규칙
- 카드에 있는 인물·장소·사건·아이템만 쓴다. 카드에 없는 것을 지어내지 않는다.
- 이미 만든 퀘스트와 겹치지 않게 한다. 겹침은 의뢰인·장소·목적(하는 일)이 **모두** 거의 같은 경우다.
  한 인물이 다른 목적으로 또 부탁하거나, 같은 장소에서 다른 일을 하는 것은 새 퀘스트다(게임에서 한 NPC가 여러 퀘스트를 주는 것은 자연스럽다).
  다만 같은 의뢰인이 같은 목적을 반복하거나, 같은 장소에서 같은 일을 반복하면 겹침이다.
- {n}개가 서로 다르게: 의뢰인, 장소, 하는 일(조사·찾기·쫓아내기·전달·의식 돕기 등)을 섞는다.
- 1장 흐름(혼자 시작한 도사가 마을 일을 풀다 다른 세계의 기사를 만나는 이야기)에 맞는 것을 고른다.
- 금지: 오니형 도깨비, 음양사 별 문양, 멸문·복수 서사.
- 이 작업실은 일일·서브 퀘스트를 만든다. 아래 메인 시나리오 사건은 제안하지 않는다(그 사건을 돕거나 앞당기거나 다시 하는 것도).
  메인 사건 사이사이 마을 사람들의 작은 일, 소문, 부탁처럼 메인과 나란히 갈 수 있는 것을 고른다.
- 기획자 지침을 따른다.
- why에는 왜 이 퀘스트가 1장에 필요한지 한 줄, card_ids에는 근거 카드 번호를 단다.
- 카드의 used_in_quests는 이미 승인된 퀘스트에서 그 카드를 쓴 횟수다. 덜 쓴 카드를 먼저 살린다(많이 쓴 카드도 목적이 다르면 쓸 수 있다).
- **억지로 {n}개를 채우지 않는다.** 이미 만든 퀘스트와 의뢰인·장소·목적이 모두 거의 같으면 새 퀘스트가 아니다.
  카드 하나에서 퀘스트 하나만 뽑고 끝내지 말고, 카드의 고민거리·성격·관계에서 다른 목적을 찾아본다.
  새롭다고 할 수 있는 것만 내고, 모자라면 needed_lore에 왜 모자란지와 어떤 설정(인물·장소·사건·내력)을 추가하면 더 만들 수 있는지 적는다.

{guide}
설정 카드
{cards}

이미 만든 퀘스트
{existing}"""


class GeminiLLM:
    name = "gemini"

    def __init__(self, model: str | None = None):
        from langchain_google_genai import ChatGoogleGenerativeAI

        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_API_KEY(또는 GEMINI_API_KEY)가 없습니다. .env에 넣어 주세요.")
        model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        # 응답이 안 오면 무한정 기다리지 않도록: 한 번 부를 때 최대 GEMINI_TIMEOUT초, 재시도 GEMINI_RETRIES번
        opts = {"timeout": float(os.getenv("GEMINI_TIMEOUT", "90")),
                "max_retries": int(os.getenv("GEMINI_RETRIES", "2"))}
        self.writer = ChatGoogleGenerativeAI(model=model, api_key=key, temperature=0.7, **opts).with_structured_output(Draft)
        self.reviewer = ChatGoogleGenerativeAI(model=model, api_key=key, temperature=0, **opts).with_structured_output(Review)
        self.planner = ChatGoogleGenerativeAI(model=model, api_key=key, temperature=0, **opts).with_structured_output(PlanReview)
        self.proposer = ChatGoogleGenerativeAI(model=model, api_key=key, temperature=0.9, **opts).with_structured_output(Proposals)

    def write(self, request, cards, chapter, feedback, previous, guide=None):
        fb = ""
        if feedback:
            fb = "\n고칠 점(이전 초안에 대한 검수·기획자 의견, 요청 문장보다 우선한다)\n" + "\n".join(f"- {f}" for f in feedback)
            if previous:
                fb += "\n\n이전 초안\n" + json.dumps(previous, ensure_ascii=False, indent=1)
        prompt = WRITER_PROMPT.format(chapter=chapter, request=request, feedback=fb, guide=guide_block(guide),
                                      cards=json.dumps(cards, ensure_ascii=False, indent=1))
        return self.writer.invoke(prompt).model_dump()

    def review(self, draft, cards, chapter, guide=None):
        g = {"main_beats": (guide or {}).get("main_beats", [])}   # 설정 검수는 사실만: 메인 사건만 넘긴다
        prompt = REVIEWER_PROMPT.format(chapter=chapter, guide=guide_block(g),
                                        cards=json.dumps(cards, ensure_ascii=False, indent=1),
                                        draft=json.dumps(draft, ensure_ascii=False, indent=1))
        return self.reviewer.invoke(prompt).model_dump()


    def plan_review(self, draft, request, cards, chapter, guide=None):
        prompt = PLAN_REVIEWER_PROMPT.format(chapter=chapter, request=request, guide=guide_block(guide),
                                             cards=json.dumps(cards, ensure_ascii=False, indent=1),
                                             draft=json.dumps(draft, ensure_ascii=False, indent=1))
        return self.planner.invoke(prompt).model_dump()

    def propose(self, cards, chapter, existing, n, guide=None):
        prompt = PROPOSER_PROMPT.format(chapter=chapter, n=n, guide=guide_block(guide),
                                        cards=json.dumps(cards, ensure_ascii=False, indent=1),
                                        existing=json.dumps(existing, ensure_ascii=False, indent=1) if existing else "없음")
        return self.proposer.invoke(prompt).model_dump()


def make_llm(kind: str = "auto") -> QuestLLM:
    """auto: 키가 있으면 Gemini, 없으면 가짜."""
    if kind == "fake":
        return FakeLLM()
    if kind == "gemini" or (kind == "auto" and (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))):
        return GeminiLLM()
    return FakeLLM()
