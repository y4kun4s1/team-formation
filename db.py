"""DB 연결 풀과 테이블 만들기. 연결은 한 번만 만들어 모든 질의에서 다시 쓴다."""

from pathlib import Path

import psycopg
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SLOTS = 4


def make_pool(url: str, wait_s: float = 15) -> ConnectionPool:
    # prepare_threshold=None: Supabase의 Transaction pooler도 쓸 수 있게 서버 쪽 준비된 질의를 끈다.
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=5,
        kwargs={"prepare_threshold": None},
        check=ConnectionPool.check_connection,
        open=True,
    )
    try:
        pool.wait(timeout=wait_s)  # 접속할 수 없으면 여기서 PoolTimeout으로 알린다
    except Exception:
        pool.close()
        raise
    return pool


def apply_schema(pool: ConnectionPool) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute("select pg_advisory_xact_lock(7770001)")  # 동시에 시작해도 한 번에 하나만 만든다
        conn.execute(sql)


def insert_member(conn: psycopg.Connection, team_id: int, student_id: int) -> bool:
    """비어 있는 가장 작은 자리에 학생을 넣는다. 자리가 없거나 이미 팀이 있으면 False."""
    for _ in range(SLOTS):
        slot = conn.execute(
            "select min(s) from generate_series(1, %s) s "
            "where s not in (select slot_no from tf_members where team_id = %s)",
            (SLOTS, team_id),
        ).fetchone()[0]
        if slot is None:
            return False
        try:
            with conn.transaction():  # 실패해도 바깥 트랜잭션은 계속 쓸 수 있게 세이브포인트로 감싼다
                conn.execute(
                    "insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
                    (team_id, slot, student_id),
                )
            return True
        except UniqueViolation as exc:
            if exc.diag.constraint_name != "tf_members_pkey":
                return False  # 이 학생은 이미 다른 팀에 있다
            # 같은 자리를 다른 요청이 먼저 차지했다. 남은 자리를 다시 찾는다.
    return False
