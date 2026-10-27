"""팀 규칙 검사. DB와 AI에 의존하지 않는 순수 함수만 둔다."""

from typing import Sequence

from models import REQUIRED_ROLES, Student

MIN_TEAM = 2
MAX_TEAM = 4

REASON_CLOSED = "모집이 마감됨"
REASON_HAS_TEAM = "이미 팀이 있음"
REASON_PENDING = "결과를 기다리는 지원이 있음"
REASON_FULL = "팀이 꽉 참"
REASON_DUP_ROLE = "같은 주 역할의 팀원이 있음"
REASON_REQUIRED = "필수 포함 역할 자리가 모자람"
REASON_SIZE = "팀 인원이 2~4명이 아님"


def can_cover(s: Student, role: str) -> bool:
    return s.main_role == role or role in s.sub_roles


def covered_required(members: Sequence[Student]) -> int:
    """필수 포함 역할(기획자, 웹 프로그래머)을 서로 다른 사람으로 채울 수 있는 최대 수(0~2)."""
    first, second = REQUIRED_ROLES
    for_first = [i for i, m in enumerate(members) if can_cover(m, first)]
    for_second = [i for i, m in enumerate(members) if can_cover(m, second)]
    if any(i != j for i in for_first for j in for_second):
        return 2
    return 1 if for_first or for_second else 0


def check_approval(applicant: Student, members: Sequence[Student]) -> str | None:
    """팀원 수, 주 역할 중복, 필수 포함 역할 자리를 검사한다. 어기면 이유, 통과하면 None."""
    if len(members) >= MAX_TEAM:
        return REASON_FULL
    if any(m.main_role == applicant.main_role for m in members):
        return REASON_DUP_ROLE
    team = [*members, applicant]
    open_slots = MAX_TEAM - len(team)
    missing = len(REQUIRED_ROLES) - covered_required(team)
    if open_slots < missing:
        return REASON_REQUIRED
    return None


def check_apply(
    applicant: Student,
    members: Sequence[Student],
    *,
    closed: bool,
    has_team: bool,
    has_pending: bool,
) -> str | None:
    if closed:
        return REASON_CLOSED
    if has_team:
        return REASON_HAS_TEAM
    if has_pending:
        return REASON_PENDING
    return check_approval(applicant, members)


def team_problem(members: Sequence[Student]) -> str | None:
    """확정된 팀이 규칙을 지키는지 확인한다. 어기면 이유, 통과하면 None."""
    if not MIN_TEAM <= len(members) <= MAX_TEAM:
        return REASON_SIZE
    mains = [m.main_role for m in members]
    if len(set(mains)) != len(mains):
        return REASON_DUP_ROLE
    if covered_required(members) < len(REQUIRED_ROLES):
        return REASON_REQUIRED
    return None
