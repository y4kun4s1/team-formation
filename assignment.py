"""AI 배정안 검사와 앱이 직접 배정하는 탐색. DB와 AI에 의존하지 않는다."""

import random
import time
from collections import Counter
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


def _can_cover_required(student: Student) -> bool:
    return any(can_cover(student, role) for role in REQUIRED_ROLES)


def _teams_open_for(teams: Mapping[int, Sequence[Student]], main_role: str) -> int:
    """이 주 역할의 학생이 들어갈 수 있는 팀(그 주 역할이 아직 없고 자리가 남은 팀)의 수."""
    return sum(
        1 for m in teams.values() if len(m) < MAX_TEAM and all(x.main_role != main_role for x in m)
    )


class SearchGaveUp(Exception):
    """탐색 한도를 넘어서, 규칙을 지키는 배정이 있는지 없는지 알 수 없을 때."""


TIME_LIMIT_S = 2.0  # 이 시간을 넘게 탐색하면 포기한다(화면이 멈추지 않게 하는 안전장치)


def _still_possible(
    members: Mapping[int, Sequence[Student]], rest: Sequence[Student]
) -> bool:
    """남은 학생(rest)으로 끝까지 갈 수 있는지 빠르게 따진다. 불가능이 확실할 때만 False(가지치기)."""
    teams_now = list(members.values())
    # 1) 팀마다 최소 인원과 필수 역할을 채우려면 최소 몇 명이 더 필요한지 센다.
    needed = sum(max(MIN_TEAM - len(m), _missing_required(m)) for m in teams_now)
    if needed > len(rest):
        return False
    # 2) 남은 학생을 모두 앉힐 자리가 있어야 한다.
    if len(rest) > sum(MAX_TEAM - len(m) for m in teams_now):
        return False
    # 3) 필수 역할을 아직 못 채운 팀마다, 그 역할을 맡을 수 있는 남은 학생이 서로 다르게 한 명씩 있어야 한다.
    for role in REQUIRED_ROLES:
        lacking = sum(1 for m in teams_now if not any(can_cover(s, role) for s in m))
        if lacking > sum(1 for s in rest if can_cover(s, role)):
            return False
    # 3-2) 팀마다 모자란 필수 역할 자리는 서로 다른 학생이 채워야 하므로, 둘 중 하나라도 맡을 수 있는 남은 학생이 그만큼 있어야 한다.
    if sum(_missing_required(m) for m in teams_now) > sum(1 for s in rest if _can_cover_required(s)):
        return False
    # 4) 같은 주 역할의 남은 학생은 그 역할이 없고 자리가 있는 팀에만 들어갈 수 있다(비둘기집).
    for role, count in Counter(s.main_role for s in rest).items():
        if count > _teams_open_for(members, role):
            return False
    # 5) 팀마다, 그 팀에 실제로 들어갈 수 있는 남은 학생(자리가 있고 주 역할이 겹치지 않는 학생)이
    #    최소 인원과 모자란 필수 역할 자리를 채울 만큼 있어야 한다.
    for m in teams_now:
        if _missing_required(m) > MAX_TEAM - len(m):  # 필수 역할 자리를 채우면 팀이 4명을 넘는다
            return False
        if len(m) >= MAX_TEAM:
            continue
        mains = {x.main_role for x in m}
        fit = [s for s in rest if s.main_role not in mains]
        if len(fit) < max(MIN_TEAM - len(m), _missing_required(m)):
            return False
        if sum(1 for s in fit if _can_cover_required(s)) < _missing_required(m):
            return False
    # 6) 필수 역할을 맡을 수 없는 남은 학생은 "필수 역할 자리가 아닌 빈자리"에만 앉을 수 있다.
    spare = sum(max(0, MAX_TEAM - len(m) - _missing_required(m)) for m in teams_now)
    if sum(1 for s in rest if not _can_cover_required(s)) > spare:
        return False
    return True


class _Restart(Exception):
    """한 번의 시도가 탐색 횟수 한도를 넘어, 학생 순서를 바꿔 다시 시작해야 할 때."""


FIRST_ATTEMPT_NODES = 1500  # 첫 시도의 탐색 횟수 한도(재시작할 때마다 두 배)


def solve_assignment(
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
    *,
    time_limit_s: float = TIME_LIMIT_S,
) -> Assignment | None:
    """규칙을 모두 지키는 배정을 끝까지 찾는다(백트래킹). 없으면 None.

    갈 수 있는 팀이 가장 적은 학생부터 놓고, 갈 곳이 없는 학생이 생기면 바로 되돌아간다.
    한곳에서 헤매지 않도록 시도마다 탐색 횟수 한도(점점 늘어남)를 두고, 넘으면 순서를 바꿔 다시 시작한다.
    한 번의 시도가 한도 안에서 끝까지 탐색하면 그 결과(배정 또는 없음)가 확정이다.
    전체 시간이 time_limit_s초를 넘으면 SearchGaveUp을 일으킨다(없다고 단정하지 않는다).
    """
    deadline = time.monotonic() + time_limit_s
    cap, attempt = FIRST_ATTEMPT_NODES, 0
    while True:
        shuffler = random.Random(attempt) if attempt else None  # 시도 번호가 씨앗이라 결과는 매번 같다
        try:
            return _search_once(teams, unassigned, cap, deadline, shuffler)
        except _Restart:
            attempt, cap = attempt + 1, cap * 2


def _search_once(
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
    node_cap: int,
    deadline: float,
    shuffler: random.Random | None,
) -> Assignment | None:
    team_ids = sorted(teams)
    members = {t: list(teams[t]) for t in team_ids}
    pending = list(unassigned)
    if shuffler is not None:
        shuffler.shuffle(pending)
    result: Assignment = {}
    visited = 0

    def open_teams(student: Student) -> list[int]:
        return [
            t for t in team_ids
            if len(members[t]) < MAX_TEAM and all(m.main_role != student.main_role for m in members[t])
        ]

    def place() -> bool:
        nonlocal visited
        visited += 1
        if visited > node_cap:
            raise _Restart
        if visited % 64 == 0 and time.monotonic() > deadline:  # 시계는 가끔만 본다
            raise SearchGaveUp
        if not _still_possible(members, pending):
            return False
        if not pending:
            return all(team_problem(members[t]) is None for t in team_ids)
        chosen = None  # (학생, 갈 수 있는 팀 목록): 갈 팀이 가장 적은 학생
        for student in pending:
            options = open_teams(student)
            if not options:
                return False
            if chosen is None or len(options) < len(chosen[1]):
                chosen = (student, options)
        student, options = chosen
        # 필수 역할을 더 채워 주는 팀, 인원이 적은 팀 순으로 시도한다. 같으면 번호순(재시작 때는 무작위).
        options.sort(key=lambda t: (
            covered_required(members[t]) - covered_required([*members[t], student]),
            len(members[t]),
            t if shuffler is None else shuffler.random(),
        ))
        index = pending.index(student)
        pending.pop(index)
        for t in options:
            members[t].append(student)
            result[student.student_no] = t
            if place():
                return True
            members[t].pop()
            del result[student.student_no]
        pending.insert(index, student)
        return False

    return dict(result) if place() else None


def explain_infeasible(
    teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]
) -> str:
    """규칙을 지키는 배정이 없을 때 사람이 읽을 이유를 돌려준다. 배정이 있으면 빈 문자열.

    아래 진단은 모두 "이것에 걸리면 배정이 반드시 없다"는 필요조건이라, 탐색보다 먼저 빠르게 검사한다.
    진단에 걸리지 않을 때만 탐색을 한 번 돌려 배정이 있는지 확인한다(탐색이 한도를 넘으면 SearchGaveUp).
    """
    for t in sorted(teams):
        mains = [m.main_role for m in teams[t]]
        if len(set(mains)) != len(mains):
            return f"팀{t}에 주 역할이 같은 팀원이 이미 있음"
        if len(mains) > MAX_TEAM:
            return f"팀{t}의 인원이 {MAX_TEAM}명을 넘음"
    shortage = sum(max(0, MIN_TEAM - len(m)) for m in teams.values())
    if shortage > len(unassigned):
        return f"학생 수가 모자라 모든 팀을 {MIN_TEAM}명 이상으로 채울 수 없음"
    if sum(MAX_TEAM - len(m) for m in teams.values()) < len(unassigned):
        return f"남은 학생이 너무 많아 모든 팀을 {MAX_TEAM}명 이하로 맞출 수 없음"
    for role, count in sorted(Counter(s.main_role for s in unassigned).items()):
        open_teams = _teams_open_for(teams, role)
        if count > open_teams:
            return f"주 역할이 {role}인 학생이 {count}명인데, 넣을 수 있는 팀 수는 {open_teams}개뿐임"
    for t in sorted(teams):
        pool = [*teams[t], *unassigned]
        for role in REQUIRED_ROLES:
            if not any(can_cover(s, role) for s in pool):
                return f"팀{t}에 {role}를 맡을 학생이 없음"
        if covered_required(pool) < len(REQUIRED_ROLES):
            return f"팀{t}의 기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"
        if _missing_required(teams[t]) > MAX_TEAM - len(teams[t]):
            return f"팀{t}은 이미 {len(teams[t])}명인데 기획자와 웹 프로그래머 자리를 채우면 {MAX_TEAM}명을 넘음"
    capable = sum(1 for s in unassigned if _can_cover_required(s))
    needed = sum(_missing_required(m) for m in teams.values())
    if needed > capable:
        return f"기획자나 웹 프로그래머를 맡을 수 있는 학생이 {capable}명뿐인데, 모든 팀을 채우려면 {needed}명이 필요함"
    if solve_assignment(teams, unassigned) is not None:
        return ""
    return "남은 학생을 어떻게 나눠 넣어도 팀 인원, 주 역할 중복, 필수 포함 역할 중 하나를 어기게 됨"
