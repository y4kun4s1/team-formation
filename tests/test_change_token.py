import pytest

from ai_client import Attempt
from scenarios import EASY, Runner
from query_count import count_queries


def _ready(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    return runner


def _closed_with_proposal(svc):
    runner = _ready(svc)
    runner.run(EASY["steps"])
    result, assignment = svc.propose_assignment(runner.admin_id, lambda s: None)
    assert result.kind == "승인", result.text
    return runner, assignment


def test_token_is_stable_without_writes(svc):
    runner = _ready(svc)
    first = svc.change_token()
    assert svc.change_token() == first
    svc.teams(), svc.unassigned(), svc.is_closed(), svc.get_student(runner.sid(1)), svc.recent_ai_logs()
    assert svc.change_token() == first


def _apply_first(svc, runner):
    assert svc.apply(runner.sid(1), 1).kind == "승인"


def _apply_pending(svc, runner):
    svc.apply(runner.sid(1), 1)
    assert svc.apply(runner.sid(4), 1).kind == "대기"


def _vote_yes(svc, runner):
    svc.apply(runner.sid(1), 1)
    svc.apply(runner.sid(2), 1)  # 팀원이 둘이 되도록 한 명 더 넣는다
    svc.apply(runner.sid(4), 1)
    application = svc.application_of(runner.sid(4))
    svc.vote(runner.sid(1), application.id, True)


def _vote_no(svc, runner):
    svc.apply(runner.sid(1), 1)
    svc.apply(runner.sid(4), 1)
    application = svc.application_of(runner.sid(4))
    assert svc.vote(runner.sid(1), application.id, False).kind == "거절"


def _close(svc, runner):
    assert svc.close(runner.admin_id).kind == "승인"


def _upload_again(svc, runner):
    assert svc.upload_roster(runner.admin_id, runner.roster, EASY["teams"]).kind == "승인"


def _ai_log(svc, runner):
    svc._log_attempt(Attempt("m1", "성공", 1.0, None))


@pytest.mark.parametrize(
    "act", [_apply_first, _apply_pending, _vote_yes, _vote_no, _close, _upload_again, _ai_log]
)
def test_token_changes_on_each_kind_of_write(svc, act):
    runner = _ready(svc)
    before = svc.change_token()
    act(svc, runner)
    assert svc.change_token() != before


def test_token_changes_on_vote_alone(svc):
    runner = _ready(svc)
    svc.apply(runner.sid(1), 1)
    svc.apply(runner.sid(2), 1)
    svc.apply(runner.sid(4), 1)
    application = svc.application_of(runner.sid(4))
    before = svc.change_token()
    svc.vote(runner.sid(1), application.id, True)  # 아직 대기라서 지원 상태는 그대로, 투표 수만 늘어난다
    assert svc.change_token() != before


def test_token_changes_on_confirm(svc):
    runner, assignment = _closed_with_proposal(svc)
    before = svc.change_token()
    assert svc.confirm(runner.admin_id, assignment).kind == "승인"
    assert svc.change_token() != before


def test_token_changes_on_close_alone(svc):
    runner = _ready(svc)
    before = svc.change_token()
    svc.close(runner.admin_id)
    assert svc.change_token() != before


def test_token_changes_when_same_size_roster_is_uploaded_again(svc):
    runner = _ready(svc)
    before = svc.change_token()
    assert svc.upload_roster(runner.admin_id, runner.roster, EASY["teams"]).kind == "승인"
    assert svc.change_token() != before


def test_token_is_one_query(svc):
    _ready(svc)
    with count_queries(svc) as counter:
        svc.change_token()
    assert counter.count == 1
