import re
import threading
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from db import apply_schema, insert_member
from scenarios import MID, Runner

SCHEMA = Path(__file__).parent.parent / "schema.sql"


def test_schema_tables_all_start_with_tf_(pool):
    names = re.findall(r"create table if not exists (\w+)", SCHEMA.read_text(encoding="utf-8"), flags=re.I)
    assert len(names) == 8 and all(n.startswith("tf_") for n in names)
    with pool.connection() as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        ).fetchall()
    assert {r[0] for r in rows} == set(names)


def test_apply_schema_can_run_twice(pool):
    apply_schema(pool)
    apply_schema(pool)


def test_check_constraints_reject_bad_roles_and_slots(svc, pool):
    Runner(svc, MID).setup()
    with pool.connection() as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "insert into tf_students(student_no, name, main_role, password_hash) "
                "values ('x1', 'x', '마법사', 'h')"
            )
    with pool.connection() as conn:
        student_id = conn.execute("select id from tf_students where student_no = '2026201'").fetchone()[0]
        with pytest.raises(errors.CheckViolation):
            conn.execute("insert into tf_members values (1, 5, %s)", (student_id,))


def test_only_one_pending_application_per_student(svc, pool):
    Runner(svc, MID).setup()
    with pool.connection() as conn:
        student_id = conn.execute("select id from tf_students where student_no = '2026201'").fetchone()[0]
        conn.execute("insert into tf_applications(team_id, student_id, status) values (1, %s, '대기')", (student_id,))
        with pytest.raises(errors.UniqueViolation):
            conn.execute("insert into tf_applications(team_id, student_id, status) values (2, %s, '대기')", (student_id,))


def test_concurrent_last_slot_only_one_wins(svc, pool):
    Runner(svc, MID).setup()
    with pool.connection() as conn:
        ids = [r[0] for r in conn.execute("select id from tf_students where not is_admin order by id").fetchall()]
        for slot, student_id in enumerate(ids[:3], start=1):
            conn.execute("insert into tf_members values (1, %s, %s)", (slot, student_id))
    contenders = ids[3:5]
    results, barrier = [], threading.Barrier(2)

    def worker(student_id):
        with pool.connection() as conn:
            barrier.wait()
            results.append(insert_member(conn, 1, student_id))

    threads = [threading.Thread(target=worker, args=(s,)) for s in contenders]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False, True]
    with pool.connection() as conn:
        assert conn.execute("select count(*) from tf_members where team_id = 1").fetchone()[0] == 4


def test_a_student_cannot_be_in_two_teams(svc, pool):
    Runner(svc, MID).setup()
    with pool.connection() as conn:
        student_id = conn.execute("select id from tf_students where student_no = '2026201'").fetchone()[0]
        assert insert_member(conn, 1, student_id) is True
        assert insert_member(conn, 2, student_id) is False
