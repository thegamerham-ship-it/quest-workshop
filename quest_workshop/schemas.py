"""작성자·검수자가 내놓는 결과의 모양(JSON 스키마)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Step(BaseModel):
    order: int = Field(description="단계 번호, 1부터")
    text: str = Field(description="플레이어가 하는 일")
    lore_refs: list[str] = Field(description="이 단계의 근거 설정 카드 번호(card_id)")


class Line(BaseModel):
    speaker: str
    line: str = Field(description="입 밖으로 내는 말만. 말이 안 통하는 장면은 '(알아들을 수 없는 외침)'처럼 짧게")
    direction: str = Field(default="", description="행동·표정 같은 연출 지문. 대사가 아닌 것은 여기에")
    lore_refs: list[str] = Field(default_factory=list, description="말투·내용의 근거 카드 번호")


class Reward(BaseModel):
    text: str
    lore_refs: list[str] = Field(default_factory=list)


class Unlock(BaseModel):
    after: str = Field(default="", description="이 메인 사건(beat_id)이 끝난 뒤에 열린다. 장 시작부터면 빈 문자열")
    before: str = Field(default="", description="이 메인 사건(beat_id)이 일어나기 전까지만 할 수 있다. 장 끝까지면 빈 문자열")
    reason: str = Field(default="", description="왜 이 시점인지 한 줄. 예: 기사와 아직 말이 통하지 않는 장면이라 귀밝이술 전")


class Draft(BaseModel):
    title: str
    summary: str = Field(description="퀘스트 한두 줄 요약")
    intent: str = Field(
        default="",
        description="이 퀘스트로 플레이어가 느끼게 할 감정 한 줄. 예: '낯선 존재에 대한 경계가 호기심으로 바뀐다'",
    )
    giver: str = Field(description="퀘스트를 주는 인물")
    unlock: Unlock = Field(default_factory=Unlock, description="메인 시나리오 기준으로 이 서브 퀘스트가 열리는 조건")
    steps: list[Step]
    dialogue: list[Line]
    reward: Reward
    questions: list[str] = Field(
        default_factory=list,
        description="퀘스트 뼈대(목표·장소·내력)가 카드에 없어 쓸 수 없을 때만 기획자에게 묻는 것. 보상·세부 연출은 묻지 않는다. 보통 빈 목록",
    )
    request_changes: list[str] = Field(
        default_factory=list,
        description="요청이 설정 카드와 부딪혀 바꿔 쓴 점. '무엇 → 무엇 (근거 card_id)' 형식. 없으면 빈 목록",
    )


class Issue(BaseModel):
    card_id: str = Field(description="부딪히는 설정 카드 번호. 없으면 빈 문자열")
    problem: str
    fix: str = Field(description="어떻게 고치면 되는지")


class Review(BaseModel):
    verdict: Literal["문제없음", "충돌", "설정에 없음"]
    issues: list[Issue] = Field(default_factory=list)


class PlanNote(BaseModel):
    point: str = Field(description="기획 관점에서 아쉬운 점")
    fix: str = Field(description="어떻게 고치면 좋은지")


class PlanReview(BaseModel):
    verdict: Literal["좋음", "고칠 점"]
    good: list[str] = Field(default_factory=list, description="잘된 점, 1~2개")
    notes: list[PlanNote] = Field(default_factory=list)


class Proposal(BaseModel):
    request: str = Field(description="작성자에게 넘길 한 줄 퀘스트 요청")
    why: str = Field(description="이 퀘스트가 이 장에 필요한 이유 한 줄")
    card_ids: list[str] = Field(default_factory=list, description="근거 설정 카드 번호")


class Proposals(BaseModel):
    items: list[Proposal] = Field(description="정말 새롭다고 할 수 있는 것만. 모자라면 적게 낸다")
    needed_lore: list[str] = Field(
        default_factory=list,
        description="요청한 개수를 못 채웠을 때: 왜 모자란지, 어떤 설정을 추가하면 더 만들 수 있는지",
    )


class OneTime(BaseModel):
    step: int = Field(description="단계 번호. 대사에 있으면 0")
    what: str = Field(description="한 번뿐이어야 하는 것. 예: 이상한 쇳조각을 처음 발견해 챙긴다")
    why: str = Field(description="반복하면 왜 어색한지. 예: 메인 단서인데 매번 처음 보는 것처럼 줍게 된다")


class RepeatFit(BaseModel):
    ok: bool = Field(description="여러 번 반복해도 어색하지 않으면 true")
    one_time: list[OneTime] = Field(default_factory=list, description="반복하면 어색해지는 한 번뿐인 사건. ok면 빈 목록")


class Variant(BaseModel):
    title: str = Field(description="변형 퀘스트 제목. 원본과 겹치지 않게")
    summary: str = Field(description="한두 줄 요약")
    giver: str = Field(description="새 의뢰인(주어진 인물 카드의 title)")
    motive: str = Field(description="이 의뢰인이 왜 이 일을 부탁하는지 한 줄. 카드의 성격·고민거리에서")
    steps: list[Step] = Field(description="원본과 같은 개수·순서·행동·완료 조건. 의뢰인이 나오는 단계는 새 의뢰인의 처지·자리에 맞게 고친다")
    dialogue: list[Line] = Field(description="새 의뢰인 말투로 새로 쓴 대사. 원본 대사를 이름만 바꿔 옮기지 않는다")
