"""팀 편성 웹 앱 화면. 규칙과 저장은 service.py가 맡고, 여기서는 화면과 Streamlit key만 맡는다.

화면 요소의 key는 자동 채점 프로그램이 찾으므로 공고 2.7절 표의 이름 그대로 써야 한다.
키와 DB 주소, 관리자 비밀번호는 화면에 출력하지 않는다.
"""

import functools
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
from service import Result, Service, blocked  # noqa: E402

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
REFRESH_SECONDS = 3  # 다른 사람의 변경을 자동으로 보여주는 간격(5초 이하여야 함)


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


# ---- 결과 안내 ----

def working(text: str, fn, *args):
    """오래 걸릴 수 있는 처리를 하는 동안 "처리 중" 표시를 보여준다."""
    with st.spinner(text):
        return fn(*args)


def finish(result: Result) -> None:
    """단추를 누른 결과를 저장하고 화면을 다시 그린다."""
    st.session_state["last_result"] = result.text
    st.rerun(scope="fragment")


def show_result() -> None:
    with st.container(key="result"):
        text = st.session_state.get("last_result")
        if text:
            st.markdown(text)


# ---- 로그인 ----

def render_login(svc: Service) -> None:
    st.title("팀 편성")
    show_result()
    st.text_input("학번", key="login_student_no")
    st.text_input("비밀번호", type="password", key="login_password")
    if st.button("로그인", key="login_submit"):
        student = svc.login(st.session_state["login_student_no"], st.session_state["login_password"])
        if student is None:
            st.session_state["last_result"] = blocked("학번 또는 비밀번호가 틀림").text
        else:
            st.session_state["student_id"] = student.id
            st.session_state["last_result"] = "승인: 로그인함"
        st.rerun()


# ---- 공통 표시 ----

def _describe(s: Student) -> str:
    subs = ", ".join(s.sub_roles) if s.sub_roles else "없음"
    return f"{s.name} · 주 역할 {s.main_role} · 보조 역할 {subs}"


def render_teams(svc: Service, me: Student, closed: bool) -> None:
    teams = svc.teams()
    my_team = next((t for t, members in teams.items() if any(m.id == me.id for m in members)), None)
    waiting = svc.application_of(me.id) if not me.is_admin else None
    for team_id, members in teams.items():
        # 팀 컨테이너에는 팀원의 이름만 넣는다(지원자의 이름을 넣으면 팀원으로 오해할 수 있다)
        with st.container(key=f"team_{team_id}", border=True):
            st.markdown(f"**팀{team_id}**" + (" (내 팀)" if team_id == my_team else ""))
            for member in members:
                st.write(_describe(member))
            if not members:
                st.caption("아직 팀원이 없음")
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
            render_votes(svc, me, my_team)


def render_votes(svc: Service, me: Student, team_id: int) -> None:
    pending = svc.pending_for_team(team_id)
    if not pending:
        return
    votes = svc.votes_by(me.id)
    with st.container(key="pending_votes"):
        st.subheader("우리 팀에 온 지원")
        for application in pending:
            st.write(f"{application.student_name}의 지원")
            if application.id in votes:
                st.write(f"내 투표: {votes[application.id]}")
                continue
            yes_col, no_col = st.columns(2)
            if yes_col.button("찬성", key=f"vote_yes_{application.student_no}"):
                finish(working("투표하는 중…", svc.vote, me.id, application.id, True))
            if no_col.button("거부", key=f"vote_no_{application.student_no}"):
                finish(working("투표하는 중…", svc.vote, me.id, application.id, False))


# ---- 관리자 ----

def render_admin(svc: Service, me: Student, closed: bool) -> None:
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
    if close_col.button("모집 마감", key="admin_close"):
        finish(working("모집을 마감하는 중…", svc.close, me.id))

    st.write("모집 상태: " + ("마감됨" if closed else "모집 중"))
    with st.container(key="unassigned"):
        st.markdown("**팀이 없는 학생**")
        left = svc.unassigned()
        for student in left:
            st.write(_describe(student))
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

    if st.button("배정안 확정", key="admin_confirm"):
        proposal = st.session_state.get("proposal")
        if proposal is None:
            finish(blocked("확정할 배정안이 없음. 먼저 AI 배정을 실행해 주세요"))
        result = working("배정안을 확정하는 중…", svc.confirm, me.id, proposal)
        if result.kind == "승인":
            for name in ("proposal", "assign_status_text"):
                st.session_state.pop(name, None)
        finish(result)


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
                st.write(f"{_describe(by_no[student_no])} → 팀{team}")


# ---- 화면 조립 ----

@st.fragment(run_every=REFRESH_SECONDS)
def live_view(svc: Service, my_id: int) -> None:
    try:
        me = svc.get_student(my_id)
        if me is None:  # 명단이 바뀌어 이 계정이 지워졌다
            st.session_state.clear()
            st.session_state["last_result"] = blocked("명단이 바뀌었으니 다시 로그인해 주세요").text
            st.rerun()
        show_result()
        closed = svc.is_closed()
        if me.is_admin:
            render_admin(svc, me, closed)
        render_teams(svc, me, closed)
        st.caption(f"마지막 갱신: {datetime.now(KST):%H:%M:%S} (한국 시각)")
    except Exception:  # noqa: BLE001 - 화면에는 안전한 문장만 보여준다
        logger.exception("live view failed")
        st.warning("DB에 접속하지 못했어요. 잠시 후 자동으로 다시 시도해요.")


def main() -> None:
    st.set_page_config(page_title="팀 편성")
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
    me = svc.get_student(student_id)
    if me is None:
        st.session_state.clear()
        st.session_state["last_result"] = blocked("명단이 바뀌었으니 다시 로그인해 주세요").text
        st.rerun()
    st.title("팀 편성")
    with st.container(key="whoami"):
        st.write(f"{me.name} ({'관리자' if me.is_admin else me.student_no})")
    if st.button("로그아웃", key="logout"):
        st.session_state.clear()
        st.rerun()
    live_view(svc, student_id)


main()
