"""화면 갱신 수용 테스트: 느린 DB(왕복 약 200ms) 뒤에서 앱이 변경을 5초 안에 보여 주고,
바뀐 게 없으면 화면을 건드리지 않으며, 흐린 요소나 중복 요소를 남기지 않는지 본다."""

import os
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import pytest

from ai_client import Attempt
from pgdelay import start_delay_proxy
from scenarios import ADMIN_PASSWORD, EASY, roster_bytes
from test_ui_keys import ROOT, TIMEOUT, key, load, login, no

playwright_sync = pytest.importorskip("playwright.sync_api")

DELAY_S = 0.1  # 한 방향 조각마다. 왕복 약 200ms


class SlowDb:
    """DB 앞의 지연 중계기를 켜고 끌 수 있게 감싼 것."""

    def __init__(self, db_url: str):
        parsed = urlparse(db_url)
        self.host, self.port = parsed.hostname, parsed.port or 5432
        self.url = db_url.replace(f":{self.port}/", ":{port}/")
        self.listen_port, self._stop = start_delay_proxy(self.host, self.port, DELAY_S)

    @property
    def app_url(self) -> str:
        return self.url.format(port=self.listen_port)

    def stop(self) -> None:
        self._stop()

    def start(self) -> None:
        _, self._stop = start_delay_proxy(self.host, self.port, DELAY_S, self.listen_port)


@pytest.fixture(scope="module")
def slow_db(db_url):
    slow = SlowDb(db_url)
    yield slow
    slow.stop()


@pytest.fixture(scope="module")
def slow_app_url(slow_db):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env.update(DATABASE_URL=slow_db.app_url, ADMIN_PASSWORD=ADMIN_PASSWORD, AI_MODELS="")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless", "true",
         "--server.port", str(port), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(120):
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
def new_page(browser, slow_app_url):
    pages = []

    def make():
        pg = browser.new_page()
        pg.goto(slow_app_url)
        pages.append(pg)
        return pg

    yield make
    for pg in pages:
        pg.close()


@pytest.fixture
def churn(svc):
    """백그라운드에서 일정 간격으로 DB를 바꿔 화면이 계속 다시 그려지게 한다(다른 사람의 활동 흉내)."""
    stop = threading.Event()
    threads = []

    def start(interval_s: float, times: int = 1000) -> threading.Thread:
        def run():
            for _ in range(times):
                if stop.wait(interval_s):
                    return
                svc._log_attempt(Attempt("m1", "성공", 1.0, None))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
        return thread

    yield start
    stop.set()
    for thread in threads:
        thread.join(timeout=5)


def _student_page(new_page, svc, number: int = 1, scenario=EASY):
    load(svc, scenario)
    page = new_page()
    login(page, no(scenario, number), "1234")
    page.wait_for_selector(key("team_1"), timeout=TIMEOUT)
    return page


def test_no_dom_change_while_nothing_changes(new_page, svc):
    page = _student_page(new_page, svc)
    page.wait_for_timeout(3000)
    page.evaluate(
        """() => {
            window.__mutations = [];
            const root = document.querySelector('[data-testid="stMain"]') || document.body;
            new MutationObserver((list) => {
                for (const m of list) {
                    const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
                    if (el && el.closest('.st-key-watcher')) continue;
                    window.__mutations.push(m.type + ':' + (el ? el.className : '?'));
                }
            }).observe(root, {childList: true, characterData: true, subtree: true});
        }"""
    )
    page.wait_for_timeout(10000)
    assert page.evaluate("window.__mutations") == []


def test_other_users_change_appears_within_5_seconds(new_page, svc):
    page = _student_page(new_page, svc, number=2)
    page.wait_for_timeout(2500)
    admin_view_wait = page.wait_for_function
    started = time.perf_counter()
    assert svc.apply(svc.login(no(EASY, 1), "1234").id, 1).kind == "승인"
    admin_view_wait(
        "() => (document.querySelector('.st-key-team_1')?.innerText || '').includes('학생1')",
        timeout=5000,
    )
    assert time.perf_counter() - started < 5.0


def test_no_stale_or_duplicate_elements_after_changes(new_page, svc, churn):
    page = _student_page(new_page, svc)
    thread = churn(2.0, times=6)
    thread.join(timeout=20)
    page.wait_for_timeout(5000)
    assert page.locator('[data-stale="true"]').count() == 0
    assert page.locator(key("team_1")).count() == 1


def test_human_speed_clicks_are_not_lost(new_page, svc, churn):
    load(svc, EASY)
    assert svc.apply(svc.login(no(EASY, 1), "1234").id, 1).kind == "승인"  # 팀1은 학생1 한 명
    applicants = [4, 5, 6, 7, 8]  # 팀1에 없는 주 역할이라 모두 지원할 수 있다
    for n in applicants:
        assert svc.apply(svc.login(no(EASY, n), "1234").id, 1).kind == "대기", n
    page = new_page()
    login(page, no(EASY, 1), "1234")
    buttons = '[class*="st-key-vote_no_"] button'
    page.wait_for_selector(buttons, timeout=TIMEOUT)
    churn(2.0)  # 다른 사람의 활동 때문에 화면이 계속 다시 그려지는 중에 누른다
    for done in range(1, len(applicants) + 1):
        clicked = time.perf_counter()
        page.locator(buttons).first.click()  # 거부는 바로 거절되어 그 지원이 목록에서 사라진다
        page.wait_for_function(
            "([sel, want]) => document.querySelectorAll(sel).length === want",
            arg=[buttons, len(applicants) - done], timeout=10000,  # 내 행동은 쓰기와 다시 그리기를 모두 거치므로 5초보다 넉넉히
        )
        page.wait_for_timeout(3000)


def test_db_outage_recovers_without_reload(new_page, svc, slow_db):
    page = _student_page(new_page, svc, number=2)
    page.wait_for_timeout(2500)
    slow_db.stop()
    try:
        assert svc.apply(svc.login(no(EASY, 1), "1234").id, 1).kind == "승인"  # 시험용 직접 연결은 살아 있다
        page.wait_for_timeout(4000)
    finally:
        slow_db.start()
    page.wait_for_function(
        "() => (document.querySelector('.st-key-team_1')?.innerText || '').includes('학생1')",
        timeout=10000,
    )


def test_admin_inputs_survive_other_users_changes(new_page, svc, tmp_path, churn):
    load(svc, EASY)
    csv_file = tmp_path / "roster.csv"
    csv_file.write_bytes(roster_bytes(EASY))
    page = new_page()
    login(page, "admin", ADMIN_PASSWORD)
    page.wait_for_selector(key("admin_roster_file"), timeout=TIMEOUT)
    page.fill(f"{key('admin_team_count')} input", "5")
    page.press(f"{key('admin_team_count')} input", "Enter")
    page.set_input_files(f"{key('admin_roster_file')} input[type=file]", str(csv_file))
    page.wait_for_selector("text=roster.csv", timeout=TIMEOUT)
    caption = page.locator("text=마지막 변경 반영")
    before = caption.inner_text()
    churn(1.0, times=1)
    page.wait_for_function(
        "(old) => { const c = [...document.querySelectorAll('p')].find(p => p.innerText.includes('마지막 변경 반영'));"
        " return c && c.innerText !== old; }",
        arg=before, timeout=8000,
    )
    assert page.input_value(f"{key('admin_team_count')} input") == "5"
    assert page.locator("text=roster.csv").count() >= 1


def test_own_change_shows_immediately_even_while_others_poll(new_page, svc):
    """내가 방금 한 변경은 다른 사람들의 확인이 한창일 때도 바로 내 화면에 보인다(옛 토큰 캐시를 다시 쓰지 않는다)."""
    load(svc, EASY)
    viewers = [new_page() for _ in range(3)]
    for index, viewer in enumerate(viewers, start=2):
        login(viewer, no(EASY, index), "1234")
        viewer.wait_for_selector(key("team_1"), timeout=TIMEOUT)
    mine = new_page()
    login(mine, no(EASY, 1), "1234")
    mine.wait_for_selector(key("apply_1"), timeout=TIMEOUT)
    mine.click(key("apply_1") + " button")
    mine.wait_for_selector(f"{key('result')} >> text=첫 팀원이 됨", timeout=15000)
    # 그리기가 끝난 화면(흐린 요소가 없는 화면)의 팀 카드에 내가 들어가 있어야 한다
    mine.wait_for_function("() => document.querySelectorAll('[data-stale=\"true\"]').length === 0", timeout=10000)
    assert "학생1" in mine.inner_text(key("team_1"))
    assert mine.locator(key("apply_1")).count() == 0
