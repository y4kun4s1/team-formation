import json
import random
import threading

import pytest

import auth
from ai_client import AiEmpty
from scenarios import ADMIN_PASSWORD, EASY, HARD, MID, Runner, assert_valid_final, roster_bytes

BOM = "﻿"


def _csv(*lines: str) -> bytes:
    return (BOM + "student_no,name,main_role,sub_roles\n" + "\n".join(lines) + "\n").encode("utf-8")


# ---- 연습용 시나리오 ----

def test_scenario_easy(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"])
    runner.finish()


def test_scenario_mid(svc):
    runner = Runner(svc, MID)
    runner.setup()
    runner.run(MID["steps"][:8])
    runner.run(MID["steps"][8:9], start=9)
    # 마감: 대기 중이던 학생12의 지원은 거절되고, 1명뿐인 팀의 학생3과 학생11은 팀에서 빠진다
    assert svc.last_application(runner.sid(12)).status == "거절"
    unassigned = {s.student_no for s in svc.unassigned()}
    assert {"2026203", "2026211", "2026212"} <= unassigned
    runner.run(MID["steps"][9:], start=10)
    runner.finish()


def test_scenario_hard(svc):
    runner = Runner(svc, HARD)
    runner.setup()
    runner.run(HARD["steps"][:13])
    runner.run(HARD["steps"][13:], start=14)
    assert svc.last_application(runner.sid(13)).status == "거절"
    assert "2026310" in {s.student_no for s in svc.unassigned()}
    runner.finish()


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("name", ["easy", "mid", "hard"])
def test_scenarios_survive_shuffled_roster(make_service, name, seed):
    scenario = {"easy": EASY, "mid": MID, "hard": HARD}[name]
    header, *rows = roster_bytes(scenario).decode("utf-8-sig").strip().splitlines()
    random.Random(seed).shuffle(rows)
    shuffled = (BOM + "\n".join([header, *rows]) + "\n").encode("utf-8")
    runner = Runner(make_service(), scenario, roster=shuffled)
    runner.setup()
    runner.run(scenario["steps"])
    runner.finish()


# ---- 로그인과 권한 ----

def test_login_handles_bad_input_without_errors(svc):
    Runner(svc, EASY).setup()
    assert svc.login(" 2026101 ", "1234") is not None  # 학번 앞뒤 공백은 지운다
    for student_no, password in [
        ("2099999", "1234"), ("2026101", ""), ("2026101", "12345"), ("2026101", " 1234 "),
        ("", ""), ("2026101", "1" * 200), ("admin", "1234"), ("2026101", ADMIN_PASSWORD),
    ]:
        assert svc.login(student_no, password) is None


def test_admin_login_and_password_rotation(make_service):
    first = make_service()
    assert first.login("admin", ADMIN_PASSWORD).is_admin is True
    from service import Service

    second = Service(first.pool, ai_call=first.ai_call, models=first.models, admin_password="new-admin-pw")
    second.ensure_admin()
    assert second.login("admin", ADMIN_PASSWORD) is None
    assert second.login("admin", "new-admin-pw").is_admin is True


def test_student_passwords_are_stored_as_bcrypt_hashes(svc, pool):
    Runner(svc, EASY).setup()
    with pool.connection() as conn:
        hashes = [r[0] for r in conn.execute("select password_hash from tf_students").fetchall()]
    assert len(hashes) == 9 and all(h.startswith("$2") and h != "1234" for h in hashes)
    assert auth.check_password("1234", hashes[0]) or auth.check_password(ADMIN_PASSWORD, hashes[0])


def test_student_cannot_use_admin_actions(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    student_id = runner.sid(1)
    assert svc.close(student_id).kind == "막힘"
    assert svc.upload_roster(student_id, roster_bytes(EASY), 3).kind == "막힘"
    result, assignment = svc.propose_assignment(student_id, lambda s: None)
    assert result.kind == "막힘" and assignment is None
    assert svc.confirm(student_id, {}).kind == "막힘"
    assert svc.is_closed() is False and len(svc.unassigned()) == 8


# ---- 명단 올리기 ----

def test_reupload_keeps_admin_and_clears_state(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    old_id = runner.sid(1)
    runner.run(EASY["steps"][:2])
    assert svc.upload_roster(runner.admin_id, roster_bytes(MID), 2).kind == "승인"
    assert set(svc.teams()) == {1, 2} and all(m == [] for m in svc.teams().values())
    assert len(svc.unassigned()) == 12 and svc.is_closed() is False
    assert svc.get_student(old_id) is None  # 지워진 학생의 세션은 더 이상 유효하지 않다
    assert svc.login("admin", ADMIN_PASSWORD) is not None


def test_bad_roster_is_rejected_and_state_is_kept(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"][:1])
    assert svc.upload_roster(runner.admin_id, _csv("1,가,마법사,"), 3).kind == "막힘"
    assert svc.upload_roster(runner.admin_id, roster_bytes(EASY), 0).kind == "막힘"
    assert svc.team_of(runner.sid(1)) == 1


# ---- 지원, 투표, 마감 ----

def test_apply_edge_cases(svc):
    runner = Runner(svc, MID)
    runner.setup()
    assert svc.apply(runner.admin_id, 1).kind == "막힘"
    assert svc.apply(runner.sid(1), 99).kind == "막힘"
    assert svc.apply(runner.sid(1), 1).kind == "승인"
    assert svc.apply(runner.sid(1), 2).kind == "막힘"  # 이미 팀이 있음


def _team_of_three(runner):
    runner.run([[("apply", 1, 1, "승인")], [("apply", 5, 1, "대기"), ("vote", 1, 5, True, "승인")]])
    assert runner.svc.apply(runner.sid(12), 1).kind == "대기"
    return runner.svc.application_of(runner.sid(12))


def test_vote_rules(svc):
    runner = Runner(svc, MID)
    runner.setup()
    application = _team_of_three(runner)
    assert svc.vote(runner.sid(9), application.id, True).kind == "막힘"  # 팀원이 아님
    assert svc.vote(runner.sid(1), application.id, True).kind == "대기"
    assert svc.vote(runner.sid(1), application.id, False).kind == "막힘"  # 투표는 바꿀 수 없음
    assert svc.votes_by(runner.sid(1))[application.id] == "찬성"
    assert svc.vote(runner.sid(5), application.id, False).kind == "거절"
    assert svc.vote(runner.sid(1), application.id, True).kind == "거절"  # 이미 끝난 지원은 지금 상태를 알려줌


def test_close_rejects_pending_and_releases_single_member_teams(svc):
    runner = Runner(svc, MID)
    runner.setup()
    runner.run([[("apply", 1, 1, "승인"), ("apply", 11, 4, "승인")], [("apply", 5, 1, "대기")]])
    assert svc.close(runner.admin_id).kind == "승인"
    assert svc.is_closed() and svc.application_of(runner.sid(5)) is None
    assert svc.last_application(runner.sid(5)).status == "거절"
    assert svc.teams()[1] == [] and svc.teams()[4] == []
    assert svc.apply(runner.sid(2), 3).kind == "막힘"
    assert svc.close(runner.admin_id).kind == "막힘"


# ---- 동시성 ----

def _race(*calls):
    results, barrier = [None] * len(calls), threading.Barrier(len(calls))

    def worker(i, call):
        barrier.wait()
        results[i] = call()

    threads = [threading.Thread(target=worker, args=(i, c)) for i, c in enumerate(calls)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_same_student_applying_to_two_teams_at_once_leaves_one_pending(svc, pool):
    runner = Runner(svc, MID)
    runner.setup()
    runner.run([[("apply", 1, 1, "승인"), ("apply", 6, 2, "승인")]])
    student_id = runner.sid(9)
    results = _race(lambda: svc.apply(student_id, 1), lambda: svc.apply(student_id, 2))
    assert sorted(r.kind for r in results) == ["대기", "막힘"]
    with pool.connection() as conn:
        count = conn.execute("select count(*) from tf_applications where student_id = %s and status = '대기'", (student_id,)).fetchone()[0]
    assert count == 1


def test_two_students_racing_for_an_empty_team_get_one_approval(svc):
    runner = Runner(svc, MID)
    runner.setup()
    results = _race(lambda: svc.apply(runner.sid(3), 3), lambda: svc.apply(runner.sid(7), 3))
    assert sorted(r.kind for r in results) == ["대기", "승인"]
    assert len(svc.teams()[3]) == 1


# ---- AI 배정 ----

def test_propose_before_close_is_blocked(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    result, assignment = svc.propose_assignment(runner.admin_id, lambda s: None)
    assert result.kind == "막힘" and assignment is None


def test_ai_total_failure_falls_back_and_confirm_saves_valid_teams(svc, pool):
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"])
    statuses = []
    result, assignment = svc.propose_assignment(runner.admin_id, statuses.append)
    assert result.kind == "승인" and "직접 배정" in result.text
    assert any("3차" in s for s in statuses)
    assert svc.confirm(runner.admin_id, assignment).kind == "승인"
    assert_valid_final(svc)
    with pool.connection() as conn:
        logs = conn.execute("select model, status from tf_ai_logs order by id").fetchall()
    assert logs == [("m1", "빈 응답"), ("m2", "빈 응답"), ("m3", "빈 응답")]


def test_valid_ai_answer_is_used_without_fallback(make_service):
    def good_ai(model, messages):
        return json.dumps({"2026107": 1, "2026108": 2})

    svc = make_service(ai_call=good_ai)
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"])
    result, assignment = svc.propose_assignment(runner.admin_id, lambda s: None)
    assert result.kind == "승인" and "직접 배정" not in result.text
    assert assignment == {"2026107": 1, "2026108": 2}


def test_no_valid_assignment_skips_the_ai_and_explains(make_service):
    calls = []
    svc = make_service(ai_call=lambda m, msgs: calls.append(m) or "{}")
    admin_id = svc.login("admin", ADMIN_PASSWORD).id
    assert svc.upload_roster(admin_id, _csv("1,가,기획자,", "2,나,웹 프로그래머,", "3,다,테스터,"), 2).kind == "승인"
    assert svc.close(admin_id).kind == "승인"
    result, assignment = svc.propose_assignment(admin_id, lambda s: None)
    assert result.kind == "막힘" and "학생 수" in result.text and assignment is None
    assert calls == []


def test_confirm_rejects_an_invalid_assignment(svc):
    runner = Runner(svc, EASY)
    runner.setup()
    runner.run(EASY["steps"])
    assert svc.confirm(runner.admin_id, {}).kind == "막힘"
    assert svc.confirm(runner.admin_id, {"2026107": 1}).kind == "막힘"
    assert len(svc.unassigned()) == 2


def test_recent_ai_logs_lists_attempts_newest_first(svc):
    from scenarios import EASY, roster_bytes

    admin = svc.login("admin", ADMIN_PASSWORD)
    assert svc.upload_roster(admin.id, roster_bytes(EASY), EASY["teams"]).kind == "승인"
    assert svc.close(admin.id).kind == "승인"
    svc.propose_assignment(admin.id, lambda text: None)  # 가짜 AI가 모두 실패한다
    logs = svc.recent_ai_logs(10)
    assert [log.model for log in logs] == ["m3", "m2", "m1"]
    assert all(log.status and log.elapsed_ms >= 0 for log in logs)
    assert len(svc.recent_ai_logs(2)) == 2
