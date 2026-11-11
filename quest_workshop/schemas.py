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
