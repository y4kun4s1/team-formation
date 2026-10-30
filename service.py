"""앱이 하는 일(로그인, 지원, 투표, 마감, 배정, 확정)을 DB 트랜잭션으로 묶는다.

규칙 검사는 rules.py와 assignment.py의 순수 함수에 맡기고, 여기서는 잠금과 저장만 책임진다.
지원, 투표, 승인은 팀 행에 FOR UPDATE 잠금을 걸고 한 번에 하나씩 처리한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

import psycopg
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

import auth
from ai_client import Attempt, run_chain
from assignment import Assignment, validate_assignment
from csv_roster import RosterError, parse_roster
from db import insert_member
from models import Student
from rules import REASON_HAS_TEAM, REASON_PENDING, check_apply, check_approval

logger = logging.getLogger(__name__)

STUDENT_PASSWORD = "1234"
ADMIN_NO = "admin"
MAX_TEAMS = 50
PENDING_INDEX = "tf_applications_one_pending"
RETRY_MESSAGE = "잠시 후 다시 시도해 주세요"


@dataclass(frozen=True)
class Result:
    kind: str  # 승인, 대기, 거절, 막힘 중 하나
    text: str  # kind로 시작하는 안내 문장


def ok(text: str) -> Result:
    return Result("승인", f"승인: {text}")


def blocked(text: str) -> Result:
    return Result("막힘", f"막힘: {text}")


@dataclass(frozen=True)
class Application:
    id: int
    team_id: int
    student_id: int
    student_no: str
    student_name: str
    status: str
    reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class AiLog:
    model: str
    status: str
    elapsed_ms: int
    error_summary: str | None
    created_at: datetime


@dataclass(frozen=True)
class CommonView:
    """모든 사람에게 똑같이 보이는 데이터. 개인 데이터는 넣지 않는다(여러 세션이 함께 캐시해서 쓰기 때문)."""

    teams: dict[int, list[Student]]
    closed: bool


@dataclass(frozen=True)
class Snapshot:
    """한 사람의 화면 하나를 그리는 데 필요한 모든 데이터. me가 None이면 나머지는 비어 있다."""

    me: Student | None
    common: CommonView
    waiting: Application | None
    last: Application | None
    pending: list[Application]
    votes: dict[int, str]
    unassigned: list[Student]  # 관리자만
    ai_logs: list[AiLog]  # 관리자만


class _Stop(Exception):
    """트랜잭션을 되돌리고 이 결과를 돌려주려고 쓰는 내부 예외."""

    def __init__(self, result: Result):
        self.result = result


_APPLICATION_SELECT = (
    "select a.id, a.team_id, a.student_id, s.student_no, s.name, a.status, a.reason, a.created_at "
    "from tf_applications a join tf_students s on s.id = a.student_id "
)

# 화면이 다시 그려야 하는지 알려 주는 "지문". 화면에 보이는 데이터가 있는 표는 모두 여기에만 적는다.
# 팀원(자리 포함), 지원 상태, 투표 수, 마감 여부, 학생 수와 최대 id(같은 인원으로 명단을 다시 올려도 바뀜),
# 팀 수, AI 기록의 최대 id.
_TOKEN_SQL = """
select md5(concat_ws('|',
  (select coalesce(string_agg(team_id::text || ':' || slot_no::text || ':' || student_id::text, ',' order by team_id, slot_no), '')
     from tf_members),
  (select coalesce(string_agg(id::text || ':' || status, ',' order by id), '') from tf_applications),
  (select count(*)::text from tf_votes),
  (select coalesce(max(value), '') from tf_settings where key = 'closed'),
  (select count(*)::text || ':' || coalesce(max(id), 0)::text from tf_students),
  (select count(*)::text from tf_teams),
  (select coalesce(max(id), 0)::text from tf_ai_logs)
))
"""


class Service:
    def __init__(
        self,
        pool: ConnectionPool,
        *,
        ai_call: Callable[[str, list[dict]], str],
        models: Sequence[str],
        admin_password: str,
    ):
        self.pool = pool
        self.ai_call = ai_call
        self.models = tuple(models)
        self.admin_password = admin_password

    # ---- 읽기 도우미 ----

    @staticmethod
    def _students(conn, ids: Sequence[int]) -> dict[int, Student]:
        if not ids:
            return {}
        id_list = list(ids)
        rows = conn.execute(
            "select id, student_no, name, main_role, is_admin from tf_students where id = any(%s)",
            (id_list,),
        ).fetchall()
        subs: dict[int, list[str]] = {}
        for student_id, role in conn.execute(
            "select student_id, role from tf_student_sub_roles where student_id = any(%s) order by role",
            (id_list,),
        ).fetchall():
            subs.setdefault(student_id, []).append(role)
        return {r[0]: Student(r[0], r[1], r[2], r[3], tuple(subs.get(r[0], ())), r[4]) for r in rows}

    def _team_members(self, conn, team_id: int) -> list[Student]:
        ids = [r[0] for r in conn.execute(
            "select student_id from tf_members where team_id = %s order by slot_no", (team_id,)
        ).fetchall()]
        students = self._students(conn, ids)
        return [students[i] for i in ids]

    def _all_teams(self, conn) -> dict[int, list[Student]]:
        teams: dict[int, list[Student]] = {
            r[0]: [] for r in conn.execute("select id from tf_teams order by id").fetchall()
        }
        rows = conn.execute("select team_id, student_id from tf_members order by team_id, slot_no").fetchall()
        students = self._students(conn, [r[1] for r in rows])
        for team_id, student_id in rows:
            teams[team_id].append(students[student_id])
        return teams

    def _unassigned(self, conn) -> list[Student]:
        ids = [r[0] for r in conn.execute(
            "select id from tf_students where not is_admin "
            "and id not in (select student_id from tf_members) order by student_no"
        ).fetchall()]
        students = self._students(conn, ids)
        return [students[i] for i in ids]

    @staticmethod
    def _closed(conn) -> bool:
        row = conn.execute("select value from tf_settings where key = 'closed'").fetchone()
        return row is not None and row[0] == "true"

    @staticmethod
    def _is_admin(conn, actor_id: int) -> bool:
        row = conn.execute("select is_admin from tf_students where id = %s", (actor_id,)).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _application(row) -> Application:
        return Application(*row)

    # ---- 읽기 ----

    def change_token(self) -> str:
        """DB의 화면용 데이터가 바뀌면 달라지는 문자열. 질의 1개만 쓴다."""
        with self.pool.connection() as conn:
            return conn.execute(_TOKEN_SQL).fetchone()[0]

    @staticmethod
    def _common(conn) -> CommonView:
        """팀과 팀원을 조인 질의 하나로 읽는다(_all_teams와 같은 결과, 왕복은 더 적음)."""
        rows = conn.execute(
            "select t.id, s.id, s.student_no, s.name, s.main_role, s.is_admin, "
            "coalesce((select array_agg(r.role order by r.role) from tf_student_sub_roles r "
            "where r.student_id = s.id), '{}') "
            "from tf_teams t left join tf_members m on m.team_id = t.id "
            "left join tf_students s on s.id = m.student_id order by t.id, m.slot_no"
        ).fetchall()
        teams: dict[int, list[Student]] = {}
        for team_id, sid, student_no, name, main_role, is_admin, subs in rows:
            members = teams.setdefault(team_id, [])
            if sid is not None:
                members.append(Student(sid, student_no, name, main_role, tuple(subs), is_admin))
        return CommonView(teams, Service._closed(conn))

    def common_view(self) -> CommonView:
        with self.pool.connection() as conn:
            return self._common(conn)

    def snapshot(self, student_id: int, common: CommonView | None = None) -> Snapshot:
        """이 사람 화면 하나에 필요한 데이터를 연결 하나로 읽는다. common을 주면 팀 목록은 다시 읽지 않는다."""
        with self.pool.connection() as conn:
            me = self._students(conn, [student_id]).get(student_id)
            if common is None:
                common = self._common(conn)
            if me is None:
                return Snapshot(None, common, None, None, [], {}, [], [])
            if me.is_admin:
                logs = conn.execute(
                    "select model, status, coalesce(elapsed_ms, 0), error_summary, created_at "
                    "from tf_ai_logs order by id desc limit 10"
                ).fetchall()
                return Snapshot(me, common, None, None, [], {}, self._unassigned(conn), [AiLog(*r) for r in logs])
            # 대기 중인 지원은 학생당 하나뿐이고 그동안 새 지원을 낼 수 없으므로, 있다면 항상 마지막 지원이다
            last = conn.execute(
                _APPLICATION_SELECT + "where a.student_id = %s order by a.id desc limit 1", (student_id,)
            ).fetchone()
            waiting = last if last is not None and last[5] == "대기" else None
            my_team = next((t for t, members in common.teams.items() if any(m.id == student_id for m in members)), None)
            pending = []
            if my_team is not None:
                pending = [self._application(r) for r in conn.execute(
                    _APPLICATION_SELECT + "where a.team_id = %s and a.status = '대기' order by a.id", (my_team,)
                ).fetchall()]
            votes = {r[0]: r[1] for r in conn.execute(
                "select application_id, vote from tf_votes where voter_id = %s", (student_id,)
            ).fetchall()}
            return Snapshot(
                me, common, self._application(waiting) if waiting else None,
                self._application(last) if last else None, pending, votes, [], [],
            )

    def get_student(self, student_id: int) -> Student | None:
        with self.pool.connection() as conn:
            return self._students(conn, [student_id]).get(student_id)

    def teams(self) -> dict[int, list[Student]]:
        with self.pool.connection() as conn:
            return self._all_teams(conn)

    def unassigned(self) -> list[Student]:
        with self.pool.connection() as conn:
            return self._unassigned(conn)

    def is_closed(self) -> bool:
        with self.pool.connection() as conn:
            return self._closed(conn)

    def team_of(self, student_id: int) -> int | None:
        with self.pool.connection() as conn:
            row = conn.execute("select team_id from tf_members where student_id = %s", (student_id,)).fetchone()
        return row[0] if row else None

    def application_of(self, student_id: int) -> Application | None:
        """이 학생의 대기 중인 지원(없으면 None)."""
        with self.pool.connection() as conn:
            row = conn.execute(
                _APPLICATION_SELECT + "where a.student_id = %s and a.status = '대기'", (student_id,)
            ).fetchone()
        return self._application(row) if row else None

    def last_application(self, student_id: int) -> Application | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                _APPLICATION_SELECT + "where a.student_id = %s order by a.id desc limit 1", (student_id,)
            ).fetchone()
        return self._application(row) if row else None

    def pending_for_team(self, team_id: int) -> list[Application]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                _APPLICATION_SELECT + "where a.team_id = %s and a.status = '대기' order by a.id", (team_id,)
            ).fetchall()
        return [self._application(r) for r in rows]

    def votes_by(self, voter_id: int) -> dict[int, str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select application_id, vote from tf_votes where voter_id = %s", (voter_id,)
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ---- 계정 ----

    def ensure_admin(self) -> None:
        if not self.admin_password:
            raise ValueError("ADMIN_PASSWORD가 비어 있음")
        with self.pool.connection() as conn:
            row = conn.execute(
                "select password_hash from tf_students where student_no = %s", (ADMIN_NO,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                    "values (%s, '관리자', '기획자', %s, true) on conflict (student_no) do nothing",
                    (ADMIN_NO, auth.hash_password(self.admin_password)),
                )
            elif not auth.check_password(self.admin_password, row[0]):
                conn.execute(
                    "update tf_students set password_hash = %s, is_admin = true where student_no = %s",
                    (auth.hash_password(self.admin_password), ADMIN_NO),
                )
            conn.execute("insert into tf_settings (key, value) values ('closed', 'false') on conflict do nothing")

    def login(self, student_no: str, password: str) -> Student | None:
        if not isinstance(student_no, str) or not isinstance(password, str):
            return None
        student_no = student_no.strip()
        if not student_no or not password:
            return None
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    "select id, password_hash from tf_students where student_no = %s", (student_no,)
                ).fetchone()
                if row is None or not auth.check_password(password, row[1]):
                    return None
                return self._students(conn, [row[0]])[row[0]]
        except psycopg.Error:
            logger.exception("login failed")
            return None

    # ---- 학생 행동 ----

    @staticmethod
    def _add_application(conn, team_id: int, student_id: int, status: str, reason: str | None) -> int:
        return conn.execute(
            "insert into tf_applications (team_id, student_id, status, reason, decided_at) "
            "values (%s, %s, %s, %s, case when %s = '대기' then null else now() end) returning id",
            (team_id, student_id, status, reason, status),
        ).fetchone()[0]

    @staticmethod
    def _decide(conn, application_id: int, status: str, reason: str | None) -> None:
        conn.execute(
            "update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
            (status, reason, application_id),
        )

    def apply(self, student_id: int, team_id: int) -> Result:
        try:
            with self.pool.connection() as conn:
                locked = conn.execute("select 1 from tf_teams where id = %s for update", (team_id,)).fetchone()
                if locked is None:
                    return blocked("없는 팀")
                applicant = self._students(conn, [student_id]).get(student_id)
                if applicant is None:
                    return blocked("로그인한 학생을 찾을 수 없음")
                if applicant.is_admin:
                    return blocked("관리자는 지원할 수 없음")
                members = self._team_members(conn, team_id)
                has_team = conn.execute(
                    "select 1 from tf_members where student_id = %s", (student_id,)
                ).fetchone() is not None
                has_pending = conn.execute(
                    "select 1 from tf_applications where student_id = %s and status = '대기'", (student_id,)
                ).fetchone() is not None
                reason = check_apply(
                    applicant, members, closed=self._closed(conn), has_team=has_team, has_pending=has_pending
                )
                if reason:
                    return blocked(reason)
                if not members:  # 빈 팀은 투표할 팀원이 없으므로 바로 승인한다
                    self._add_application(conn, team_id, student_id, "승인", None)
                    if not insert_member(conn, team_id, student_id):
                        raise _Stop(blocked(REASON_HAS_TEAM))
                    return ok(f"팀{team_id}의 첫 팀원이 됨")
                self._add_application(conn, team_id, student_id, "대기", None)
                return Result("대기", f"대기: 팀{team_id} 팀원의 투표를 기다림")
        except _Stop as stop:
            return stop.result
        except UniqueViolation as exc:
            # 같은 학생의 지원이 동시에 들어온 경우: DB의 유일성 제약이 한쪽만 통과시킨다
            return blocked(REASON_PENDING if exc.diag.constraint_name == PENDING_INDEX else REASON_HAS_TEAM)
        except psycopg.Error:
            logger.exception("apply failed")
            return blocked(RETRY_MESSAGE)

    def vote(self, voter_id: int, application_id: int, yes: bool) -> Result:
        try:
            with self.pool.connection() as conn:
                row = conn.execute("select team_id from tf_applications where id = %s", (application_id,)).fetchone()
                if row is None:
                    return blocked("없는 지원")
                team_id = row[0]
                conn.execute("select 1 from tf_teams where id = %s for update", (team_id,)).fetchone()
                applicant_id, status = conn.execute(
                    "select student_id, status from tf_applications where id = %s", (application_id,)
                ).fetchone()
                if status != "대기":
                    return Result(status, f"{status}: 이미 결과가 나온 지원임")
                members = self._team_members(conn, team_id)
                member_ids = {m.id for m in members}
                if voter_id not in member_ids:
                    return blocked("팀원만 투표할 수 있음")
                if conn.execute(
                    "select 1 from tf_votes where application_id = %s and voter_id = %s", (application_id, voter_id)
                ).fetchone():
                    return blocked("이미 투표했음(바꿀 수 없음)")
                conn.execute(
                    "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                    (application_id, voter_id, "찬성" if yes else "거부"),
                )
                if not yes:  # 한 명이라도 거부하면 다른 투표를 기다리지 않고 바로 거절한다
                    self._decide(conn, application_id, "거절", "팀원이 거부함")
                    return Result("거절", "거절: 팀원이 거부함")
                yes_voters = {r[0] for r in conn.execute(
                    "select voter_id from tf_votes where application_id = %s and vote = '찬성'", (application_id,)
                ).fetchall()}
                if not member_ids <= yes_voters:
                    return Result("대기", "대기: 아직 투표하지 않은 팀원이 있음")
                # 승인 직전에 규칙을 다시 검사한다. 같은 팀의 앞선 지원이 먼저 승인돼 팀이 바뀌었을 수 있다.
                applicant = self._students(conn, [applicant_id])[applicant_id]
                reason = check_approval(applicant, members)
                if reason is None and not insert_member(conn, team_id, applicant_id):
                    reason = "자리가 없거나 이미 팀이 있음"
                if reason:
                    self._decide(conn, application_id, "거절", reason)
                    return Result("거절", f"거절: {reason}")
                self._decide(conn, application_id, "승인", None)
                return ok(f"팀{team_id}의 팀원이 됨")
        except psycopg.Error:
            logger.exception("vote failed")
            return blocked(RETRY_MESSAGE)

    # ---- 관리자 행동 ----

    def upload_roster(self, actor_id: int, data: bytes, team_count: int) -> Result:
        with self.pool.connection() as conn:
            if not self._is_admin(conn, actor_id):
                return blocked("관리자만 명단을 올릴 수 있음")
        try:
            rows = parse_roster(data)
        except RosterError as exc:
            return blocked(str(exc))
        if isinstance(team_count, bool) or not isinstance(team_count, int) or not 1 <= team_count <= MAX_TEAMS:
            return blocked(f"팀 수는 1~{MAX_TEAMS} 사이여야 함")
        hashes = [auth.hash_password(STUDENT_PASSWORD) for _ in rows]
        try:
            with self.pool.connection() as conn:
                conn.execute("select pg_advisory_xact_lock(7770002)")
                for table in ("tf_votes", "tf_applications", "tf_members", "tf_teams", "tf_settings"):
                    conn.execute(f"delete from {table}")
                conn.execute("delete from tf_students where not is_admin")
                conn.execute("insert into tf_settings (key, value) values ('closed', 'false')")
                for number in range(1, team_count + 1):
                    conn.execute("insert into tf_teams (id, name) values (%s, %s)", (number, f"팀{number}"))
                for row, hashed in zip(rows, hashes):
                    student_id = conn.execute(
                        "insert into tf_students (student_no, name, main_role, password_hash) "
                        "values (%s, %s, %s, %s) returning id",
                        (row.student_no, row.name, row.main_role, hashed),
                    ).fetchone()[0]
                    for role in row.sub_roles:
                        conn.execute(
                            "insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (student_id, role)
                        )
            return ok(f"학생 {len(rows)}명과 팀 {team_count}개로 시작함")
        except psycopg.Error:
            logger.exception("upload_roster failed")
            return blocked(RETRY_MESSAGE)

    def close(self, actor_id: int) -> Result:
        try:
            with self.pool.connection() as conn:
                if not self._is_admin(conn, actor_id):
                    return blocked("관리자만 마감할 수 있음")
                conn.execute("select id from tf_teams order by id for update").fetchall()  # 진행 중인 처리가 끝나길 기다린다
                if self._closed(conn):
                    return blocked("이미 마감됨")
                conn.execute("update tf_settings set value = 'true' where key = 'closed'")
                conn.execute(
                    "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
                    "where status = '대기'"
                )
                conn.execute(  # 1명뿐인 팀의 학생은 팀에서 빠진다
                    "delete from tf_members where team_id in "
                    "(select team_id from tf_members group by team_id having count(*) = 1)"
                )
                return ok("모집을 마감함")
        except psycopg.Error:
            logger.exception("close failed")
            return blocked(RETRY_MESSAGE)

    def _log_attempt(self, attempt: Attempt) -> None:
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "insert into tf_ai_logs (model, status, elapsed_ms, error_summary) values (%s, %s, %s, %s)",
                    (attempt.model, attempt.status, int(attempt.elapsed_s * 1000), (attempt.error or "")[:200] or None),
                )
        except psycopg.Error:
            logger.warning("could not log AI attempt")

    def recent_ai_logs(self, limit: int = 10) -> list[AiLog]:
        """최근 AI 호출 기록(새것이 먼저). 키와 응답 본문은 저장하지 않으므로 그대로 보여 줘도 된다."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select model, status, coalesce(elapsed_ms, 0), error_summary, created_at "
                "from tf_ai_logs order by id desc limit %s",
                (limit,),
            ).fetchall()
        return [AiLog(*r) for r in rows]

    def propose_assignment(
        self, actor_id: int, on_status: Callable[[str], None]
    ) -> tuple[Result, Assignment | None]:
        with self.pool.connection() as conn:
            if not self._is_admin(conn, actor_id):
                return blocked("관리자만 배정할 수 있음"), None
            if not self._closed(conn):
                return blocked("먼저 모집을 마감해야 함"), None
            teams = self._all_teams(conn)
            left = self._unassigned(conn)
        chain = run_chain(
            teams, left, models=self.models, call=self.ai_call, on_status=on_status, on_attempt=self._log_attempt
        )
        if chain.source == "none":
            if chain.assignment == {}:
                return ok("배정할 학생이 없음"), {}
            if chain.gave_up:  # 없다고 단정할 수 없으므로 "배정이 없음"이라고 말하지 않는다
                return blocked(f"배정을 찾지 못함: {chain.note}"), None
            return blocked(f"규칙을 지키는 배정이 없음: {chain.note}"), None
        if chain.source == "ai":
            return ok("AI 배정안이 규칙 검사를 통과함"), chain.assignment
        return ok("AI가 실패해 앱이 직접 배정함(규칙 검사를 통과함)"), chain.assignment

    def confirm(self, actor_id: int, assignment: Assignment) -> Result:
        try:
            with self.pool.connection() as conn:
                if not self._is_admin(conn, actor_id):
                    return blocked("관리자만 배정을 확정할 수 있음")
                conn.execute("select id from tf_teams order by id for update").fetchall()
                if not self._closed(conn):
                    return blocked("모집을 마감한 뒤에 확정할 수 있음")
                teams = self._all_teams(conn)
                left = self._unassigned(conn)
                problem = validate_assignment(assignment, teams, left)
                if problem:
                    return blocked(problem)
                by_no = {s.student_no: s for s in left}
                for student_no in sorted(assignment):
                    value = assignment[student_no]
                    team_id = value[0] if isinstance(value, (list, tuple)) else value
                    if not insert_member(conn, team_id, by_no[student_no].id):
                        raise _Stop(blocked("자리가 부족해 저장하지 못함"))
                return ok("배정을 확정함")
        except _Stop as stop:
            return stop.result
        except psycopg.Error:
            logger.exception("confirm failed")
            return blocked(RETRY_MESSAGE)
