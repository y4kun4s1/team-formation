import os

import pytest

from ai_client import AiEmpty
from scenarios import ADMIN_PASSWORD

# 로컬에서만 쓰는 테스트용 DB 주소다. 실제 Supabase 주소가 아니다.
DEFAULT_TEST_DB = "postgresql://tf:tf_test_pw@127.0.0.1:5432/tf_test"
TABLES = (
    "tf_votes, tf_applications, tf_members, tf_teams, tf_student_sub_roles, "
    "tf_students, tf_settings, tf_ai_logs"
)


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB)


@pytest.fixture(scope="session")
def pool(db_url):
    from db import apply_schema, make_pool

    pool = make_pool(db_url)
    apply_schema(pool)
    yield pool
    pool.close()


def _failing_ai(model, messages):
    raise AiEmpty("빈 응답")


@pytest.fixture
def make_service(pool):
    """깨끗한 DB에서 시작하는 서비스를 만든다. ai_call을 바꿔 끼울 수 있다."""
    from service import Service

    def factory(ai_call=_failing_ai, admin_password=ADMIN_PASSWORD, models=("m1", "m2", "m3")):
        with pool.connection() as conn:
            conn.execute(f"truncate {TABLES} restart identity cascade")
        svc = Service(pool, ai_call=ai_call, models=models, admin_password=admin_password)
        svc.ensure_admin()
        return svc

    return factory


@pytest.fixture
def svc(make_service):
    return make_service()
