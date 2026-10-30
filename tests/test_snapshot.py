import dataclasses

import pytest

from ai_client import Attempt
from query_count import count_queries
from scenarios import EASY, Runner
from service import CommonView


@pytest.fixture
def world(svc):
    """팀원·대기 지원·거절된 지원·투표가 섞인 상태. 학생 n은 EASY 명단의 n번째 학생."""
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"][:4])  # 마지막 단계에서 학생7이 거절됨. 마감 전
    svc.apply(runner.sid(6), 3)  # 학생6: 팀3(학생3 한 명뿐)에 대기
    svc._log_attempt(Attempt("m1", "성공", 1.0, None))
    return svc, runner


def test_common_view_equals_individual_getters(world):
    svc, _ = world
    common = svc.common_view()
    assert common.teams == svc.teams()
    assert common.closed == svc.is_closed()


@pytest.mark.parametrize("n", [1, 3, 4, 6, 7, 8])  # 팀원, 투표할 팀원, 팀원, 대기 중, 거절당함, 팀도 지원도 없음
def test_student_snapshot_equals_individual_getters(world, n):
    svc, runner = world
    student_id = runner.sid(n)
    snap = svc.snapshot(student_id)
    team_id = svc.team_of(student_id)
    assert snap.me == svc.get_student(student_id)
    assert snap.waiting == svc.application_of(student_id)
    assert snap.last == svc.last_application(student_id)
    assert snap.pending == (svc.pending_for_team(team_id) if team_id else [])
    assert snap.votes == svc.votes_by(student_id)
    assert snap.unassigned == [] and snap.ai_logs == []
    assert snap.common == svc.common_view()


def test_snapshot_uses_given_common_view(world):
    svc, runner = world
    common = svc.common_view()
    with count_queries(svc) as with_common:
        svc.snapshot(runner.sid(1), common)
    with count_queries(svc) as without:
        svc.snapshot(runner.sid(1))
    assert with_common.count < without.count


def test_admin_snapshot_has_unassigned_and_ai_logs(world):
    svc, runner = world
    snap = svc.snapshot(runner.admin_id)
    assert snap.me.is_admin
    assert snap.unassigned == svc.unassigned()
    assert snap.ai_logs == svc.recent_ai_logs(10)
    assert snap.waiting is None and snap.last is None and snap.pending == [] and snap.votes == {}


def test_snapshot_of_deleted_student_is_empty(world):
    svc, _ = world
    snap = svc.snapshot(999999)
    assert snap.me is None
    assert snap.waiting is None and snap.pending == [] and snap.votes == {} and snap.unassigned == []


def test_common_view_contains_no_personal_data():
    assert [f.name for f in dataclasses.fields(CommonView)] == ["teams", "closed"]


def test_query_budget(world):
    svc, runner = world
    with count_queries(svc) as c:
        common = svc.common_view()
    assert c.count <= 3
    with count_queries(svc) as c:
        svc.snapshot(runner.sid(1), common)
    assert c.count <= 8
    with count_queries(svc) as c:
        svc.snapshot(runner.admin_id, common)
    assert c.count <= 12
