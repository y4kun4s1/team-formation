from helpers import student
from rules import (
    REASON_CLOSED,
    REASON_DUP_ROLE,
    REASON_FULL,
    REASON_HAS_TEAM,
    REASON_PENDING,
    REASON_REQUIRED,
    REASON_SIZE,
    can_cover,
    check_apply,
    check_approval,
    covered_required,
    team_problem,
)

NO_STATE = dict(closed=False, has_team=False, has_pending=False)


def test_can_cover_uses_main_or_sub_role():
    s = student(1, "TT", "PL")
    assert can_cover(s, "테스터") and can_cover(s, "기획자")
    assert not can_cover(s, "웹 프로그래머")


def test_covered_required_counts_distinct_people():
    both = student(1, "PL", "WP")
    assert covered_required([]) == 0
    assert covered_required([both]) == 1
    assert covered_required([both, student(2, "TT")]) == 1
    assert covered_required([both, student(3, "WP")]) == 2


def test_required_shortage_blocks_report_writer_on_two_person_team():
    members = [student(1, "TT"), student(2, "PT")]
    assert check_apply(student(3, "RW"), members, **NO_STATE) == REASON_REQUIRED


def test_scenario_hard_step11_blocks_with_required_reason():
    members = [student(11, "TT", "PT"), student(15, "PT", "FD")]
    assert check_apply(student(14, "RW", "TT"), members, **NO_STATE) == REASON_REQUIRED


def test_scenario_mid_step3_blocks_same_main_role():
    members = [student(1, "PL", "PT;TT"), student(5, "WP", "TT")]
    assert check_apply(student(2, "PL", "TT"), members, **NO_STATE) == REASON_DUP_ROLE


def test_approval_recheck_rejects_late_duplicate():
    members = [student(2, "PL", "PT"), student(8, "FD", "TT"), student(5, "WP", "FD")]
    assert check_approval(student(6, "WP", "RW"), members) == REASON_DUP_ROLE


def test_full_team_blocks_before_other_role_checks():
    members = [student(i, r) for i, r in enumerate(["PL", "WP", "TT", "PT"], start=1)]
    assert check_apply(student(9, "RW"), members, **NO_STATE) == REASON_FULL


def test_state_checks_come_first_in_order():
    s, members = student(9, "RW"), [student(1, "TT"), student(2, "PT")]
    assert check_apply(s, members, closed=True, has_team=True, has_pending=True) == REASON_CLOSED
    assert check_apply(s, members, closed=False, has_team=True, has_pending=True) == REASON_HAS_TEAM
    assert check_apply(s, members, closed=False, has_team=False, has_pending=True) == REASON_PENDING


def test_valid_applications_pass():
    assert check_apply(student(4, "WP", "FD"), [student(1, "PL", "RW")], **NO_STATE) is None
    assert check_apply(student(1, "PL"), [], **NO_STATE) is None


def test_team_problem_checks_size_duplicates_and_required_roles():
    assert team_problem([student(1, "PL"), student(2, "WP")]) is None
    assert team_problem([student(1, "PL", "WP")]) == REASON_SIZE
    assert team_problem([student(i, "TT") for i in range(1, 6)]) == REASON_SIZE
    assert team_problem([student(1, "PL"), student(2, "PL", "WP")]) == REASON_DUP_ROLE
    assert team_problem([student(1, "PL", "WP"), student(2, "TT")]) == REASON_REQUIRED
