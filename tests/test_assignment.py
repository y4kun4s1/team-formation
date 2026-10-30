import time

import pytest

from assignment import explain_infeasible, solve_assignment, validate_assignment
from helpers import EASY_LEFT, EASY_TEAMS, student
from rules import REASON_DUP_ROLE, REASON_REQUIRED, REASON_SIZE, team_problem

def _final_teams(teams, left, assignment):
    by_no = {s.student_no: s for s in left}
    out = {t: list(m) for t, m in teams.items()}
    for no, t in assignment.items():
        out[t].append(by_no[no])
    return out


def test_solver_result_passes_validator_and_every_team_is_valid():
    result = solve_assignment(EASY_TEAMS, EASY_LEFT)
    assert result is not None
    assert set(result) == {"7", "8"}
    assert validate_assignment(result, EASY_TEAMS, EASY_LEFT) is None
    finals = _final_teams(EASY_TEAMS, EASY_LEFT, result)
    assert all(team_problem(m) is None for m in finals.values())


def test_solver_returns_empty_when_nothing_to_assign_and_teams_are_valid():
    assert solve_assignment(EASY_TEAMS, []) == {}


def test_solver_returns_none_when_an_existing_team_is_already_broken():
    teams = {1: [student(1, "PL"), student(2, "PL", "WP")]}
    assert solve_assignment(teams, []) is None


def test_solver_never_moves_existing_members():
    result = solve_assignment(EASY_TEAMS, EASY_LEFT)
    existing = {s.student_no for m in EASY_TEAMS.values() for s in m}
    assert existing.isdisjoint(result)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"7": 1}, "빠진 학생"),
        ({"7": [1, 2], "8": 3}, "여러 팀"),
        ({"7": 1, "8": 2, "99": 1}, "없는 학번"),
        ({"7": 1, "8": 9}, "없는 팀 번호"),
        ({"7": 1, "8": 2, "1": 2}, "기존 팀원"),
        ({"7": "1", "8": 2}, "형식"),
    ],
)
def test_validator_rejects_each_violation(raw, expected):
    reason = validate_assignment(raw, EASY_TEAMS, EASY_LEFT)
    assert reason is not None and expected in reason


def test_validator_rejects_duplicate_main_role_in_a_team():
    left = [student(7, "TT"), student(8, "TT")]
    reason = validate_assignment({"7": 1, "8": 1}, EASY_TEAMS, left)
    assert REASON_DUP_ROLE in reason and "팀1" in reason


def test_validator_rejects_team_over_four_members():
    teams = {1: [student(i, r) for i, r in enumerate(["PL", "WP", "TT", "PT"], start=1)]}
    reason = validate_assignment({"9": 1}, teams, [student(9, "RW")])
    assert REASON_SIZE in reason


def test_validator_rejects_missing_required_role():
    teams = {1: [student(1, "PL"), student(2, "TT")], 2: [student(3, "PL"), student(4, "RW")]}
    left = [student(7, "WP"), student(8, "PT")]
    reason = validate_assignment({"7": 1, "8": 2}, teams, left)
    assert REASON_REQUIRED in reason and "팀2" in reason


def test_infeasible_when_no_candidate_for_required_role():
    teams = {1: [student(1, "PL"), student(2, "TT")]}
    left = [student(3, "RW")]
    assert solve_assignment(teams, left) is None
    reason = explain_infeasible(teams, left)
    assert "팀1" in reason and "웹 프로그래머" in reason


def test_infeasible_when_not_enough_students_for_minimum_team_size():
    teams = {1: [], 2: []}
    left = [student(1, "PL", "WP")]
    assert solve_assignment(teams, left) is None
    assert "학생 수" in explain_infeasible(teams, left)


def test_explain_is_empty_when_a_valid_assignment_exists():
    assert explain_infeasible(EASY_TEAMS, EASY_LEFT) == ""


def test_solver_finishes_quickly_even_when_infeasible_with_eight_students():
    teams = {t: [] for t in (1, 2, 3, 4)}
    left = [student(i, "TT", "PL;WP") for i in range(1, 9)]
    start = time.monotonic()
    assert solve_assignment(teams, left) is None
    assert time.monotonic() - start < 5


def test_generic_infeasible_reason_does_not_repeat_prefix():
    from assignment import explain_infeasible, solve_assignment
    from helpers import student

    # 팀 하나에 같은 주 역할의 학생 둘만 남아 있다: 어느 진단에도 안 걸리지만 배정은 불가능하다.
    teams = {1: []}
    left = [student(1, "PL", "WP"), student(2, "PL", "WP")]
    assert solve_assignment(teams, left) is None
    reason = explain_infeasible(teams, left)
    assert reason and not reason.startswith("규칙을 지키는 배정이 없음")
