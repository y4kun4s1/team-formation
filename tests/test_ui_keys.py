"""화면 테스트: 실제 브라우저(Playwright)로 앱을 열어 공고 2.7절의 key가 있는지, 흐름이 되는지 본다."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scenarios import ADMIN_PASSWORD, EASY, MID, roster_bytes

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 20000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app_url(db_url):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env.update(DATABASE_URL=db_url, ADMIN_PASSWORD=ADMIN_PASSWORD, AI_MODELS="")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless", "true",
         "--server.port", str(port), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.5)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, app_url):
    pg = browser.new_page()
    pg.goto(app_url)
    yield pg
    pg.close()


def key(name: str) -> str:
    return f".st-key-{name}"


def login(page, student_no: str, password: str) -> None:
    page.wait_for_selector(key("login_submit"), timeout=TIMEOUT)
    page.fill(f"{key('login_student_no')} input", student_no)
    page.fill(f"{key('login_password')} input", password)
    page.click(f"{key('login_submit')} button")
    page.wait_for_selector(key("whoami"), timeout=TIMEOUT)


def result_text(page) -> str:
    return page.inner_text(key("result")).strip()


def load(svc, scenario):
    admin = svc.login("admin", ADMIN_PASSWORD)
    assert svc.upload_roster(admin.id, roster_bytes(scenario), scenario["teams"]).kind == "승인"
    return admin


def no(scenario, n: int) -> str:
    return f"{scenario['prefix']}{n:02d}"


def test_login_screen_has_keys(page, svc):
    for name in ("login_student_no", "login_password", "login_submit"):
        assert page.wait_for_selector(key(name), timeout=TIMEOUT)


def test_wrong_password_is_blocked(page, svc):
    load(svc, EASY)
    login_fail = lambda: page.wait_for_selector(key("login_submit"), timeout=TIMEOUT)  # noqa: E731
    login_fail()
    page.fill(f"{key('login_student_no')} input", no(EASY, 1))
    page.fill(f"{key('login_password')} input", "틀린비번")
    page.click(f"{key('login_submit')} button")
    page.wait_for_function("document.querySelector('.st-key-result')?.innerText.includes('막힘')", timeout=TIMEOUT)


def test_admin_screen_has_keys(page, svc):
    load(svc, EASY)
    login(page, "admin", ADMIN_PASSWORD)
    for name in ("admin_roster_file", "admin_team_count", "admin_start", "admin_close", "unassigned",
                 "admin_assign", "assign_status", "admin_confirm", "team_1", "team_2", "team_3"):
        assert page.wait_for_selector(key(name), timeout=TIMEOUT), name
    assert result_text(page).startswith("승인")


def test_student_apply_and_vote_flow(page, svc):
    admin = load(svc, MID)
    ids = {n: svc.login(no(MID, n), "1234").id for n in (1, 5, 12)}
    assert svc.apply(ids[1], 1).kind == "승인"
    login(page, no(MID, 5), "1234")
    page.wait_for_selector(key("apply_1"), timeout=TIMEOUT)
    assert page.query_selector(key("apply_2"))
    page.click(f"{key('apply_1')} button")
    page.wait_for_function("document.querySelector('.st-key-result')?.innerText.length > 0", timeout=TIMEOUT)
    assert result_text(page).split(":")[0] in ("승인", "대기", "거절", "막힘")
    page.wait_for_function("!document.querySelector('.st-key-apply_1')", timeout=TIMEOUT)
    # 팀원 1번이 투표해서 승인되면 5번은 팀원이 된다. 새로고침 없이 자동으로 바뀌어야 한다.
    app = svc.last_application(ids[5])
    assert app.status == "대기"
    assert svc.vote(ids[1], app.id, True).kind == "승인"
    page.wait_for_function("document.querySelector('.st-key-team_1')?.innerText.includes('(내 팀)')", timeout=12000)
    assert admin is not None


def test_pending_applicant_not_shown_as_member(page, svc):
    load(svc, MID)
    a, b = svc.login(no(MID, 1), "1234"), svc.login(no(MID, 5), "1234")
    assert svc.apply(a.id, 1).kind == "승인"
    assert svc.apply(b.id, 1).kind == "대기"
    login(page, no(MID, 1), "1234")
    page.wait_for_selector(key("team_1"), timeout=TIMEOUT)
    assert b.name not in page.inner_text(key("team_1"))
    page.wait_for_selector(key(f"vote_yes_{b.student_no}"), timeout=TIMEOUT)
    page.click(f"{key(f'vote_yes_{b.student_no}')} button")
    page.wait_for_function(
        f"!document.querySelector('.st-key-vote_yes_{b.student_no}')", timeout=TIMEOUT)


def test_deleted_student_returns_to_login(page, svc):
    admin = load(svc, EASY)
    login(page, no(EASY, 1), "1234")
    assert svc.upload_roster(admin.id, roster_bytes(MID), MID["teams"]).kind == "승인"  # 명단 교체
    page.wait_for_selector(key("login_submit"), timeout=12000)


def test_no_secret_on_screen(page, svc):
    load(svc, EASY)
    login(page, "admin", ADMIN_PASSWORD)
    page.wait_for_selector(key("admin_start"), timeout=TIMEOUT)
    text = page.inner_text("body")
    for secret in ("tf_test_pw", ADMIN_PASSWORD, "postgresql://"):
        assert secret not in text


def test_admin_full_flow(page, svc, tmp_path):
    svc_admin = svc.login("admin", ADMIN_PASSWORD)
    assert svc_admin is not None
    login(page, "admin", ADMIN_PASSWORD)
    csv = tmp_path / "roster.csv"
    csv.write_bytes(roster_bytes(EASY))
    page.set_input_files(f"{key('admin_roster_file')} input[type=file]", str(csv))
    page.wait_for_function(
        "document.querySelector('.st-key-admin_roster_file')?.innerText.includes('roster.csv')", timeout=TIMEOUT)
    page.click(f"{key('admin_start')} button")
    page.wait_for_function("document.querySelector('.st-key-result')?.innerText.includes('학생 8명')", timeout=TIMEOUT)
    assert len(svc.unassigned()) == 8
    page.click(f"{key('admin_close')} button")
    page.wait_for_function("document.body.innerText.includes('마감됨')", timeout=TIMEOUT)
    page.click(f"{key('admin_assign')} button")
    page.wait_for_selector(key("proposal"), timeout=60000)
    assert "직접 배정" in page.inner_text(key("assign_status"))
    page.click(f"{key('admin_confirm')} button")
    page.wait_for_function("!document.querySelector('.st-key-proposal')", timeout=TIMEOUT)
    assert svc.unassigned() == []


def test_typed_team_count_survives_refresh(page, svc):
    login(page, "admin", ADMIN_PASSWORD)
    box = f"{key('admin_team_count')} input"
    page.wait_for_selector(box, timeout=TIMEOUT)
    page.fill(box, "7")
    page.press(box, "Enter")
    time.sleep(8)  # 자동 갱신이 두 번 이상 지나간다
    assert page.input_value(box) == "7"
