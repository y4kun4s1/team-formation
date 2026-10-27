"""OpenRouter 무료 모델로 배정안을 받아 오는 부분. 실패하면 다음 모델로, 끝까지 실패하면 앱이 직접 배정한다."""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import requests

from assignment import Assignment, explain_infeasible, solve_assignment, validate_assignment
from models import Student

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 8000
DEADLINE_S = 120.0

# 서로 다른 회사의 무료 모델 3개(1차, 2차, 3차). OpenRouter 무료 목록에서 골랐고,
# 실제 키로 동작을 확인하기 전까지는 잠정이다. `openrouter/free` 라우터는 쓰지 않는다.
DEFAULT_MODELS: tuple[str, ...] = (
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling:free",
)


class AiError(Exception):
    """AI 호출이나 응답 처리에 실패했을 때의 공통 예외. 메시지에는 키나 응답 본문을 넣지 않는다."""


class AiTimeout(AiError):
    pass


class AiEmpty(AiError):
    pass


class AiHttpError(AiError):
    pass


class AiBadResponse(AiError):
    pass


def _describe(s: Student) -> str:
    subs = ", ".join(s.sub_roles) if s.sub_roles else "없음"
    return f"{s.student_no}(주 역할: {s.main_role}, 보조 역할: {subs})"


def build_messages(
    teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]
) -> list[dict]:
    team_lines = [
        f"팀{t}: " + (", ".join(_describe(s) for s in teams[t]) if teams[t] else "(빈 팀)")
        for t in sorted(teams)
    ]
    left_lines = [_describe(s) for s in unassigned]
    example = "{" + ", ".join(f'"{s.student_no}": {sorted(teams)[0]}' for s in unassigned[:2]) + "}"
    system = (
        "너는 팀 배정 도우미다. 팀이 없는 학생을 기존 팀에 한 명씩 배정한다.\n"
        "규칙:\n"
        "1. 팀이 없는 학생을 한 명도 빠뜨리지 말고, 한 학생은 한 팀에만 넣는다.\n"
        "2. 이미 팀에 있는 학생은 옮기지 않는다.\n"
        "3. 모든 팀은 최종 2~4명이어야 한다.\n"
        "4. 한 팀 안에 주 역할이 같은 학생이 둘 이상 있으면 안 된다.\n"
        "5. 모든 팀에 기획자와 웹 프로그래머를 맡을 수 있는 학생이 서로 다른 사람으로 있어야 한다."
        " 주 역할이나 보조 역할로 맡을 수 있다.\n"
        '답은 설명 없이 JSON 객체 하나만 쓴다. 키는 학번(문자열), 값은 팀 번호(정수)이다. 예: ' + example
    )
    user = "현재 팀:\n" + "\n".join(team_lines) + "\n\n팀이 없는 학생:\n" + "\n".join(left_lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_assignment(text: str) -> dict:
    """모델의 답에서 {학번: 팀 번호} 객체를 꺼낸다. 코드 펜스와 앞뒤 설명문은 걸러낸다."""
    if not isinstance(text, str) or not text.strip():
        raise AiBadResponse("빈 응답")
    candidates = [text.strip()]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    parsed = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            parsed = value
            break
    if parsed is None:
        raise AiBadResponse("JSON 객체를 찾을 수 없음")

    result: dict = {}
    for key, value in parsed.items():
        if isinstance(value, bool):
            raise AiBadResponse(f"팀 번호를 읽을 수 없음({key})")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
        elif isinstance(value, list) and value and all(
            isinstance(v, int) and not isinstance(v, bool) for v in value
        ):
            number = list(value)
        else:
            raise AiBadResponse(f"팀 번호를 읽을 수 없음({key})")
        result[str(key)] = number
    return result


def call_model(
    model: str,
    messages: list[dict],
    *,
    api_key: str,
    deadline_s: float = DEADLINE_S,
    post: Callable = requests.post,
) -> str:
    """모델 하나를 부른다. 응답을 기다리는 시간은 deadline_s 초를 넘기지 않는다."""
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "reasoning": {"effort": "low"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    box: dict = {}

    def work() -> None:
        try:
            box["response"] = post(
                API_URL, headers=headers, json=body, timeout=(10, deadline_s + 10)
            )
        except BaseException as exc:  # 스레드 밖에서 처리하려고 보관한다
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise AiTimeout(f"{deadline_s:g}초 안에 응답하지 않음")

    error = box.get("error")
    if isinstance(error, requests.exceptions.Timeout):
        raise AiTimeout("응답 시간이 초과됨")
    if error is not None:
        raise AiHttpError(f"연결 오류({type(error).__name__})")

    response = box["response"]
    if response.status_code != 200:
        raise AiHttpError(f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise AiBadResponse("JSON이 아닌 응답") from None
    if isinstance(data, dict) and data.get("error"):
        raise AiHttpError("모델 오류 응답")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AiEmpty("응답에 내용이 없음") from None
    if not isinstance(content, str) or not content.strip():
        raise AiEmpty("빈 응답")
    return content


@dataclass
class Attempt:
    model: str
    status: str  # 성공, 시간 초과, 빈 응답, 오류, 형식 오류, 검사 실패
    elapsed_s: float
    error: str | None


@dataclass
class ChainResult:
    assignment: Assignment | None
    source: str  # "ai", "solver", "none"
    attempts: list[Attempt] = field(default_factory=list)
    note: str = ""  # 규칙을 지키는 배정이 없을 때의 이유


def _label(exc: AiError) -> str:
    if isinstance(exc, AiTimeout):
        return "시간 초과"
    if isinstance(exc, AiEmpty):
        return "빈 응답"
    if isinstance(exc, AiHttpError):
        return "오류"
    return "검사 실패" if str(exc).startswith("검사 실패") else "형식 오류"


def run_chain(
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
    *,
    models: Sequence[str],
    call: Callable[[str, list[dict]], str],
    on_status: Callable[[str], None],
    on_attempt: Callable[[Attempt], None],
) -> ChainResult:
    """모델을 1차, 2차, 3차 순으로 부르고, 모두 실패하면 앱이 직접 찾은 배정을 쓴다."""
    if not unassigned:
        return ChainResult({}, "none")

    fallback = solve_assignment(teams, unassigned)
    if fallback is None:
        reason = explain_infeasible(teams, unassigned)
        on_status(f"규칙을 지키는 배정이 없음: {reason}")
        return ChainResult(None, "none", note=reason)

    messages = build_messages(teams, unassigned)
    attempts: list[Attempt] = []
    for tier, model in enumerate(models, start=1):
        on_status(f"{tier}차 모델 응답 대기 중({model})")
        started = time.monotonic()
        try:
            raw = parse_assignment(call(model, messages))
            problem = validate_assignment(raw, teams, unassigned)
            if problem:
                raise AiBadResponse(f"검사 실패: {problem}")
        except AiError as exc:
            attempt = Attempt(model, _label(exc), time.monotonic() - started, str(exc))
            attempts.append(attempt)
            on_attempt(attempt)
            continue
        attempt = Attempt(model, "성공", time.monotonic() - started, None)
        attempts.append(attempt)
        on_attempt(attempt)
        return ChainResult(raw, "ai", attempts)

    on_status("AI가 모든 모델에서 실패해 앱이 직접 배정함")
    return ChainResult(fallback, "solver", attempts)
