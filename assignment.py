"""AI 배정안 검사와 앱이 직접 배정하는 탐색. DB와 AI에 의존하지 않는다."""

from typing import Mapping, Sequence

from models import REQUIRED_ROLES, Student
from rules import MAX_TEAM, MIN_TEAM, can_cover, covered_required, team_problem

Assignment = dict[str, int]  # 학번 -> 팀 번호


def _team_numbers(value: object) -> list[int] | None:
    """값이 팀 번호 하나 또는 팀 번호 목록이면 목록으로 돌려주고, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)) and value:
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return list(value)
    return None


def validate_assignment(
    raw: Mapping[str, object],
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
) -> str | None:
    """배정안이 규칙을 지키는지 검사한다. 어기면 이유, 통과하면 None."""
    if not isinstance(raw, Mapping):
        return "배정 형식이 맞지 않음"
    chosen: dict[str, list[int]] = {}
    for key, value in raw.items():
        numbers = _team_numbers(value)
        if not isinstance(key, str) or numbers is None:
            return f"배정 형식이 맞지 않음({key})"
        chosen[key] = numbers

    existing = {s.student_no for members in teams.values() for s in members}
    left_by_no = {s.student_no: s for s in unassigned}
    for key, numbers in chosen.items():
        if key in existing:
            return f"기존 팀원({key})을 다른 팀으로 옮길 수 없음"
        if key not in left_by_no:
            return f"없는 학번({key})이 있음"
        if len(numbers) != 1:
            return f"한 학생({key})이 여러 팀에 배정됨"
        if numbers[0] not in teams:
            return f"없는 팀 번호({numbers[0]})가 있음"

    missing = [s.student_no for s in unassigned if s.student_no not in chosen]
    if missing:
        return f"빠진 학생이 있음({', '.join(missing)})"

    final = {t: list(members) for t, members in teams.items()}
    for key, numbers in chosen.items():
        final[numbers[0]].append(left_by_no[key])
    for t in sorted(final):
        problem = team_problem(final[t])
        if problem:
            return f"팀{t}: {problem}"
    return None


def _missing_required(members: Sequence[Student]) -> int:
    return len(REQUIRED_ROLES) - covered_required(members)


def solve_assignment(
    teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]
) -> Assignment | None:
    """규칙을 모두 지키는 배정을 끝까지 찾는다(백트래킹). 없으면 None."""
    team_ids = sorted(teams)
    members = {t: list(teams[t]) for t in team_ids}
    order = list(unassigned)
    result: Assignment = {}

    def can_still_finish(next_index: int) -> bool:
        # 팀마다 최소 인원과 필수 역할을 채우려면 최소 몇 명이 더 필요한지 센다.
        needed = sum(
            max(MIN_TEAM - len(m), _missing_required(m)) for m in members.values()
        )
        return needed <= len(order) - next_index

    def place(i: int) -> bool:
        if not can_still_finish(i):
            return False
        if i == len(order):
            return all(team_problem(members[t]) is None for t in team_ids)
        student = order[i]
        for t in sorted(team_ids, key=lambda t: (len(members[t]), t)):
            team = members[t]
            if len(team) >= MAX_TEAM:
                continue
            if any(m.main_role == student.main_role for m in team):
                continue
            team.append(student)
            result[student.student_no] = t
            if place(i + 1):
                return True
            team.pop()
            del result[student.student_no]
        return False

    return dict(result) if place(0) else None


def explain_infeasible(
    teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]
) -> str:
    """규칙을 지키는 배정이 없을 때 사람이 읽을 이유를 돌려준다. 배정이 있으면 빈 문자열."""
    if solve_assignment(teams, unassigned) is not None:
        return ""
    for t in sorted(teams):
        mains = [m.main_role for m in teams[t]]
        if len(set(mains)) != len(mains):
            return f"팀{t}에 주 역할이 같은 팀원이 이미 있음"
        if len(mains) > MAX_TEAM:
            return f"팀{t}의 인원이 {MAX_TEAM}명을 넘음"
    shortage = sum(max(0, MIN_TEAM - len(m)) for m in teams.values())
    if shortage > len(unassigned):
        return f"학생 수가 모자라 모든 팀을 {MIN_TEAM}명 이상으로 채울 수 없음"
    for t in sorted(teams):
        pool = [*teams[t], *unassigned]
        for role in REQUIRED_ROLES:
            if not any(can_cover(s, role) for s in pool):
                return f"팀{t}에 {role}를 맡을 학생이 없음"
        if covered_required(pool) < len(REQUIRED_ROLES):
            return f"팀{t}의 기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"
    return "남은 학생을 어떻게 나눠 넣어도 팀 인원, 주 역할 중복, 필수 포함 역할 중 하나를 어기게 됨"
