"""팀 편성 웹 앱 화면. 규칙과 저장은 service.py가 맡고, 여기서는 화면과 Streamlit key만 맡는다.

화면 요소의 key는 자동 채점 프로그램이 찾으므로 공고 2.7절 표의 이름 그대로 써야 한다.
키와 DB 주소, 관리자 비밀번호는 화면에 출력하지 않는다.
"""

import functools
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")  # 이미 설정된 환경변수(배포 환경의 Secrets)는 덮어쓰지 않는다
load_dotenv(HERE / "key.env")

from ai_client import DEFAULT_MODELS, AiHttpError, call_model  # noqa: E402
from db import apply_schema, make_pool  # noqa: E402
from models import Student  # noqa: E402
from rules import covered_required  # noqa: E402
from service import CommonView, Result, Service, Snapshot, blocked  # noqa: E402

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
POLL_SECONDS = 2  # 다른 사람의 변경이 있는지 확인하는 간격. 토큰 캐시 1초와 그리는 시간을 더해도 5초 이하여야 함


class ConfigError(Exception):
    """필요한 설정이 빠졌을 때. 메시지에는 설정 이름만 넣고 값은 넣지 않는다."""


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        found = st.secrets.get(name)
    except Exception:
        return None
    return str(found) if found else None


def _models() -> tuple[str, ...]:
    """AI_MODELS(쉼표로 구분)로 모델을 바꿀 수 있다. 무료(:free) 모델 3개가 아니면 기본값을 쓴다."""
    raw = _secret("AI_MODELS")
    if raw:
        names = tuple(m.strip() for m in raw.split(",") if m.strip())
        if len(names) >= 1 and all(m.endswith(":free") and m != "openrouter/free" for m in names):
            return names
        logger.warning("AI_MODELS ignored: every model must end with :free and not be openrouter/free")
    return DEFAULT_MODELS


def _ai_call():
    api_key = _secret("OPENROUTER_API_KEY")
    if not api_key:
        def missing(model, messages):
            raise AiHttpError("OPENROUTER_API_KEY가 설정되지 않음")
        return missing
    return functools.partial(call_model, api_key=api_key)


@st.cache_resource(show_spinner=False)
def get_service() -> Service:
    """연결 풀과 서비스를 한 번만 만들어 모든 화면이 다시 쓴다."""
    url, admin_password = _secret("DATABASE_URL"), _secret("ADMIN_PASSWORD")
    missing = [n for n, v in (("DATABASE_URL", url), ("ADMIN_PASSWORD", admin_password)) if not v]
    if missing:
        raise ConfigError(", ".join(missing))
    pool = make_pool(url)
    apply_schema(pool)
    service = Service(pool, ai_call=_ai_call(), models=_models(), admin_password=admin_password)
    service.ensure_admin()
    return service


@st.cache_data(ttl=1, show_spinner=False)
def _shared_token(_svc: Service) -> str:
    """DB가 바뀌었는지 보는 지문. 모든 세션이 1초 동안 하나를 같이 써서 DB 질의가 초당 약 1개로 유지된다."""
    return _svc.change_token()


@st.cache_data(ttl=30, max_entries=4, show_spinner=False)
def _shared_common(_svc: Service, token: str) -> CommonView:
    """모든 사람에게 같은 데이터(팀 목록, 마감 여부). 토큰이 바뀌면 새로 읽는다. 개인 데이터는 넣지 않는다."""
    return _svc.common_view()


def _prefetch(svc: Service, student_id: int, *, fresh: bool = False) -> None:
    """이 사람 화면에 필요한 데이터를 읽어 세션에 둔다. 느린 DB 읽기는 앱을 그리는 도중이 아니라 여기서 끝낸다.
    그리는 도중에 읽으면 그 시간 내내 화면이 흐리게 보인다.
    fresh=True는 내가 방금 쓴 직후에 쓴다. 공유 토큰 캐시를 거치지 않고 DB에서 바로 읽어야, 다른 사람의 확인이 쓰기 전에
    시작해 캐시에 넣어 둔 옛 토큰 때문에 옛 팀 목록이 다시 보이는 일이 없다."""
    token = svc.change_token() if fresh else _shared_token(svc)
    st.session_state["prefetched"] = (token, svc.snapshot(student_id, _shared_common(svc, token)))


# ---- 모양(회색·버건디·검정) ----

STYLE = """
<style>
.stApp { background: #F3F2F2; }
h1 { color: #1B1B1D; letter-spacing: -0.5px; }
h1::after { content: ""; display: block; width: 48px; height: 4px; margin-top: 8px; background: #7A1F2B; border-radius: 2px; }
[class*="st-key-team_"] { background: #FFFFFF; border: 1px solid #D4D1D1 !important; border-left: 5px solid #8E8A8A !important;
  border-radius: 10px; padding: 12px 16px; margin-bottom: 8px; }
[class*="st-key-team_"]:has(.mine-marker) { border-left: 5px solid #7A1F2B !important; background: #FBF4F5; }
.badge { display: inline-block; padding: 1px 9px; margin: 0 4px 2px 0; border-radius: 999px; font-size: 0.82rem; }
.badge-main { background: #7A1F2B; color: #FFFFFF; }
.badge-sub { background: #D9D6D6; color: #1B1B1D; }
.badge-ok { background: #1B1B1D; color: #FFFFFF; }
.badge-warn { background: #FFFFFF; color: #7A1F2B; border: 1px solid #7A1F2B; }
.member-row { margin: 4px 0; }
.member-name { font-weight: 600; margin-right: 8px; }
.steps { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px; }
.step { padding: 4px 12px; border-radius: 8px; background: #E4E2E2; color: #6B6767; font-size: 0.9rem; }
.step-done { background: #1B1B1D; color: #FFFFFF; }
.step-now { background: #7A1F2B; color: #FFFFFF; font-weight: 600; }
.mine-marker { display: none; }
.st-key-watcher { display: none; }
.stButton button:hover { border-color: #7A1F2B; color: #7A1F2B; }
</style>
"""


def badge(text: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{html.escape(text)}</span>'


def member_row(s: Student) -> str:
    subs = "".join(badge(r, "sub") for r in s.sub_roles) or '<span style="color:#6B6767">없음</span>'
    return (
        f'<div class="member-row"><span class="member-name">{html.escape(s.name)}</span>'
        f'주 역할 {badge(s.main_role, "main")} 보조 역할 {subs}</div>'
    )


def steps_html(current: int) -> str:
    names = ["① 명단 올리기", "② 모집 마감", "③ AI 배정", "④ 배정 확정"]
    parts = []
    for i, name in enumerate(names, 1):
        css = "step-done" if i < current else "step-now" if i == current else ""
        parts.append(f'<span class="step {css}">{name}</span>')
    return '<div class="steps">' + "".join(parts) + "</div>"


# ---- 결과 안내 ----

def working(text: str, fn, *args):
    """오래 걸릴 수 있는 처리를 하는 동안 "처리 중" 표시를 보여준다."""
    with st.spinner(text):
        return fn(*args)


def finish(result: Result) -> None:
    """단추를 누른 결과를 저장하고 화면을 다시 그린다. 방금 바꾼 내용이 보이도록 공유 토큰을 비운다."""
    st.session_state["last_result"] = result.text
    _shared_token.clear()
    st.session_state.pop("prefetched", None)
    try:  # 방금 바꾼 내용을 지금 읽어 둔다. 실패하면 다음에 그릴 때 다시 읽는다
        _prefetch(get_service(), st.session_state["student_id"], fresh=True)
    except Exception:  # noqa: BLE001
        logger.warning("prefetch after action failed")
    st.rerun()


def show_result() -> None:
    with st.container(key="result"):
        text = st.session_state.get("last_result")
        if text:
            kind = text.split(":", 1)[0]
            if kind == "승인":
                st.success(text)
            elif kind == "대기":
                st.info(text)
            else:  # 거절, 막힘
                st.error(text)


# ---- 로그인 ----

def render_login(svc: Service) -> None:
    st.title("팀 편성")
    show_result()
    st.caption("학번과 비밀번호를 입력해 로그인하세요.")
    with st.form("login_form", border=False):  # 폼이어야 비밀번호 칸에서 Enter를 눌러도 로그인된다
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit")
    if submitted:
        student = svc.login(st.session_state["login_student_no"], st.session_state["login_password"])
        if student is None:
            st.session_state["last_result"] = blocked("학번 또는 비밀번호가 틀림").text
        else:
            st.session_state["student_id"] = student.id
            st.session_state["last_result"] = "승인: 로그인함"
        st.rerun()


# ---- 공통 표시 ----

def render_teams(svc: Service, snap: Snapshot) -> None:
    me, teams, closed, waiting = snap.me, snap.common.teams, snap.common.closed, snap.waiting
    my_team = next((t for t, members in teams.items() if any(m.id == me.id for m in members)), None)
    if not me.is_admin:
        render_my_status(me, my_team, waiting, closed)
        render_last_rejection(snap, my_team)
    st.subheader("팀 목록")
    for team_id, members in teams.items():
        # 팀 컨테이너에는 팀원의 이름만 넣는다(지원자의 이름을 넣으면 팀원으로 오해할 수 있다)
        with st.container(key=f"team_{team_id}", border=True):
            if team_id == my_team:
                st.markdown('<span class="mine-marker"></span>', unsafe_allow_html=True)
            st.markdown(f"**팀{team_id}**" + (" (내 팀)" if team_id == my_team else ""))
            for member in members:
                st.markdown(member_row(member), unsafe_allow_html=True)
            if not members:
                st.caption("아직 팀원이 없음")
            else:
                full = len(members) >= 4
                need = covered_required(members) < 2
                chips = badge(f"{len(members)}/4명", "ok" if full else "sub")
                chips += badge("필수 역할 보강 필요", "warn") if need else badge("필수 역할 충족", "ok")
                if full:
                    chips += badge("꽉 참", "warn")
                st.markdown(chips, unsafe_allow_html=True)
            can_apply = not me.is_admin and my_team is None and waiting is None and not closed
            if can_apply and st.button(f"팀{team_id}에 지원", key=f"apply_{team_id}"):
                finish(working("지원하는 중…", svc.apply, me.id, team_id))
    if not me.is_admin:
        if closed and my_team is None:
            st.warning("모집이 마감되어 직접 지원할 수 없어요. 관리자가 배정하면 팀이 정해져요.")
        if my_team is None:
            st.write("아직 팀이 없음")
        if waiting is not None:
            st.write(f"내 지원: 팀{waiting.team_id}의 결과를 기다리는 중")
        if my_team is not None:
            render_votes(svc, snap, my_team)


def render_last_rejection(snap: Snapshot, my_team: int | None) -> None:
    """팀이 없고 기다리는 지원도 없을 때, 마지막 지원이 거절됐으면 이유를 알려준다."""
    if my_team is not None or snap.waiting is not None:
        return
    last = snap.last
    if last is None or last.status != "거절":
        return
    with st.container(key="my_application"):
        st.warning(f"팀{last.team_id}에 낸 지원이 거절됨: {last.reason or '이유가 기록되지 않음'}")


def render_my_status(me: Student, my_team: int | None, waiting, closed: bool) -> None:
    """학생이 지금 무엇을 하면 되는지 한 줄로 알려준다."""
    subs = ", ".join(me.sub_roles) if me.sub_roles else "없음"
    st.caption(f"내 역할: 주 역할 {me.main_role} · 보조 역할 {subs}")
    if my_team is not None:
        st.info(f"팀{my_team}에 들어갔어요. 우리 팀에 지원이 오면 찬성이나 거부를 눌러 주세요.")
    elif waiting is not None:
        st.info(f"팀{waiting.team_id}에 지원했어요. 그 팀 팀원의 투표를 기다리고 있어요.")
    elif closed:
        st.info("모집이 끝났어요. 관리자의 배정을 기다려 주세요.")
    else:
        st.info("원하는 팀의 '지원' 단추를 눌러 팀에 들어가세요.")


def render_votes(svc: Service, snap: Snapshot, team_id: int) -> None:
    me, pending, votes = snap.me, snap.pending, snap.votes
    if not pending:
        return
    with st.container(key="pending_votes"):
        st.subheader("우리 팀에 온 지원")
        for application in pending:
            st.markdown(f"<b>{html.escape(application.student_name)}</b>의 지원", unsafe_allow_html=True)
            if application.id in votes:
                st.write(f"내 투표: {votes[application.id]}")
                continue
            yes_col, no_col = st.columns(2)
            if yes_col.button("찬성", key=f"vote_yes_{application.student_no}"):
                finish(working("투표하는 중…", svc.vote, me.id, application.id, True))
            if no_col.button("거부", key=f"vote_no_{application.student_no}"):
                finish(working("투표하는 중…", svc.vote, me.id, application.id, False))


# ---- 관리자 ----

def render_admin(svc: Service, snap: Snapshot) -> None:
    me, teams, closed, left_now = snap.me, snap.common.teams, snap.common.closed, snap.unassigned
    placed = sum(len(m) for m in teams.values())
    if not teams:
        current = 1
    elif not closed:
        current = 2
    elif left_now:
        current = 4 if st.session_state.get("proposal") else 3
    else:
        current = 5
    with st.container(key="admin_steps"):
        st.markdown(steps_html(current), unsafe_allow_html=True)
        st.caption(f"팀 {len(teams)}개 · 팀이 있는 학생 {placed}명 · 팀이 없는 학생 {len(left_now)}명")
    st.file_uploader("명단 CSV", type=["csv"], key="admin_roster_file")
    st.number_input("팀 수", min_value=1, max_value=50, value=3, step=1, key="admin_team_count")
    start_col, close_col = st.columns(2)
    if start_col.button("명단으로 처음 상태 만들기", key="admin_start"):
        uploaded = st.session_state.get("admin_roster_file")
        if uploaded is None:
            finish(blocked("명단 CSV를 먼저 올려 주세요"))
        result = working("명단으로 처음 상태를 만드는 중…", svc.upload_roster, me.id, uploaded.getvalue(), int(st.session_state["admin_team_count"]))
        if result.kind == "승인":
            for name in ("proposal", "assign_status_text"):
                st.session_state.pop(name, None)
        finish(result)
    start_col.caption("누르면 기존 팀, 지원, 투표가 모두 지워져요.")
    if close_col.button("모집 마감", key="admin_close"):
        finish(working("모집을 마감하는 중…", svc.close, me.id))

    st.write("모집 상태: " + ("마감됨" if closed else "모집 중"))
    with st.container(key="unassigned"):
        st.markdown("**팀이 없는 학생**")
        left = snap.unassigned
        for student in left:
            st.markdown(member_row(student), unsafe_allow_html=True)
        if not left:
            st.caption("없음")

    assign_clicked = st.button("AI 배정", key="admin_assign")
    with st.container(key="assign_status"):
        slot = st.empty()
        stored = st.session_state.get("assign_status_text")
        slot.markdown(stored or "AI 배정 상태: 아직 실행하지 않음")  # 비어 있으면 화면에서 안 보이므로 항상 한 줄을 둔다
    if assign_clicked:
        last: list[str] = []

        def on_status(text: str) -> None:
            last.append(text)
            slot.markdown(text)  # 몇 차 모델의 응답을 기다리는 중인지 바로 보여준다

        slot.markdown("AI 배정을 시작하는 중… (모델당 최대 2분이 걸릴 수 있어요)")
        result, assignment = working("AI 배정 중… 창을 닫지 말고 기다려 주세요", svc.propose_assignment, me.id, on_status)
        st.session_state["proposal"] = assignment
        st.session_state["assign_status_text"] = (
            result.text if result.kind == "승인" else (last[-1] if last else result.text)
        )
        finish(result)
    render_proposal(left)
    render_ai_logs(snap.ai_logs)

    if st.button("배정안 확정", key="admin_confirm"):
        proposal = st.session_state.get("proposal")
        if proposal is None:
            finish(blocked("확정할 배정안이 없음. 먼저 AI 배정을 실행해 주세요"))
        result = working("배정안을 확정하는 중…", svc.confirm, me.id, proposal)
        if result.kind == "승인":
            for name in ("proposal", "assign_status_text"):
                st.session_state.pop(name, None)
        finish(result)


def render_ai_logs(logs: list) -> None:
    """최근 AI 호출 기록. 어느 모델이 얼마나 걸려 어떻게 끝났는지 본다."""
    with st.container(key="ai_logs"):
        st.markdown("**최근 AI 호출 기록**")
        if not logs:
            st.caption("아직 AI를 부른 기록이 없음")
            return
        lines = ["| 시각(한국) | 모델 | 결과 | 걸린 시간 | 이유 |", "|---|---|---|---|---|"]
        for log in logs:
            when = log.created_at.astimezone(KST).strftime("%m-%d %H:%M:%S")
            reason = html.escape((log.error_summary or "").replace("|", "/").replace("\n", " "))
            lines.append(f"| {when} | `{log.model}` | {log.status} | {log.elapsed_ms / 1000:.1f}초 | {reason} |")
        st.markdown("\n".join(lines))


def render_proposal(left: list[Student]) -> None:
    proposal = st.session_state.get("proposal")
    if not proposal:
        return
    by_no = {s.student_no: s for s in left}
    with st.container(key="proposal"):
        st.markdown("**배정안(확정 전)**")
        for student_no, value in sorted(proposal.items()):
            team = value[0] if isinstance(value, (list, tuple)) else value
            if student_no in by_no:
                st.markdown(f"{member_row(by_no[student_no])}→ **팀{team}**", unsafe_allow_html=True)


# ---- 화면 조립 ----

@st.fragment(run_every=POLL_SECONDS)
def watcher(svc: Service) -> None:
    """눈에 안 보이는 감시자. 2초마다 변경 토큰만 보고, 화면에 그린 때와 달라졌을 때만 새 데이터를 미리 읽어 두고
    앱 전체를 다시 그린다. 느린 DB 읽기를 여기서 끝내 두므로, 본문은 읽기 없이 곧바로 그려져 흐려지는 시간이 짧다."""
    if st.session_state.pop("inline_watch", False):
        # 본문을 그린 직후 같은 실행 안에서 불린 것이다. 여기서 st.rerun()으로 끝나면 그 실행이 정상 종료되지 못해
        # 지워져야 할 옛 요소가 화면에 남는 것을 시험으로 확인했다. 그래서 2초 간격의 자체 실행에서만 확인한다.
        return
    student_id = st.session_state.get("student_id")
    try:
        token = _shared_token(svc)
        if student_id is None or token == st.session_state.get("seen_token"):
            return
        _prefetch(svc, student_id)
    except Exception:  # noqa: BLE001 - DB가 잠시 안 되면 화면은 그대로 두고 다음 확인 때 다시 본다
        logger.warning("change check failed")
        return
    st.rerun()


def _logout_to_login() -> None:
    st.session_state.clear()
    st.session_state["last_result"] = blocked("명단이 바뀌었으니 다시 로그인해 주세요").text
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="팀 편성")
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        svc = get_service()
    except ConfigError as exc:
        st.error(f"설정이 빠져 있음: {exc}")
        st.stop()
    except Exception:  # noqa: BLE001
        logger.exception("startup failed")
        st.error("DB에 접속할 수 없음. 잠시 후 다시 시도해 주세요.")
        st.stop()

    student_id = st.session_state.get("student_id")
    if student_id is None:
        render_login(svc)
        return
    st.title("팀 편성")
    try:
        if "prefetched" not in st.session_state:  # 첫 화면이거나 내가 방금 바꾼 뒤: 여기서 읽는다
            _prefetch(svc, student_id)
        token, snap = st.session_state["prefetched"]
        if snap.me is None:  # 명단이 바뀌어 이 계정이 지워졌다
            _logout_to_login()
        me = snap.me
        with st.container(key="whoami"):
            st.write(f"{me.name} ({'관리자' if me.is_admin else me.student_no})")
        if st.button("로그아웃", key="logout"):
            st.session_state.clear()
            st.rerun()
        show_result()
        if me.is_admin:
            render_admin(svc, snap)
        render_teams(svc, snap)
        st.caption(f"마지막 변경 반영: {datetime.now(KST):%H:%M:%S} (한국 시각)")
        st.session_state["seen_token"] = token  # 끝까지 그린 뒤에만 기록해야, 실패하면 감시자가 다시 시도한다
    except Exception:  # noqa: BLE001 - 화면에는 안전한 문장만 보여준다
        logger.exception("render failed")
        st.session_state.pop("seen_token", None)
        st.session_state.pop("prefetched", None)
        st.warning("DB에 접속하지 못했어요. 잠시 후 자동으로 다시 시도해요.")
    with st.container(key="watcher"):
        st.session_state["inline_watch"] = True
        watcher(svc)


main()
