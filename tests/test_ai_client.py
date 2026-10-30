import json
import time

import pytest
import requests

from ai_client import (
    DEFAULT_MODELS,
    AiBadResponse,
    AiEmpty,
    AiHttpError,
    AiTimeout,
    build_messages,
    call_model,
    parse_assignment,
    run_chain,
)
from assignment import validate_assignment
from helpers import EASY_LEFT, EASY_TEAMS, student

GOOD = json.dumps({"7": 1, "8": 2})


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _ok(content):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def test_default_models_are_three_distinct_free_models_without_router():
    assert len(DEFAULT_MODELS) == 3 and len(set(DEFAULT_MODELS)) == 3
    assert all(m.endswith(":free") for m in DEFAULT_MODELS)
    assert "openrouter/free" not in DEFAULT_MODELS


def test_build_messages_lists_teams_students_and_asks_for_json_only():
    messages = build_messages(EASY_TEAMS, EASY_LEFT)
    text = "\n".join(m["content"] for m in messages)
    for team_no in EASY_TEAMS:
        assert f"팀{team_no}" in text
    for s in EASY_LEFT:
        assert s.student_no in text and s.main_role in text
    assert "JSON" in text and "2~4명" in text


@pytest.mark.parametrize(
    "text",
    [
        '{"7": 1, "8": 2}',
        '```json\n{"7": 1, "8": 2}\n```',
        '결과는 다음과 같습니다: {"7": 1, "8": 2} 입니다.',
        '{"7": "1", "8": "2"}',
    ],
)
def test_parse_assignment_handles_fences_prose_and_numeric_strings(text):
    assert parse_assignment(text) == {"7": 1, "8": 2}


@pytest.mark.parametrize("text", ["", "배정할 수 없습니다", "{not json}", "[1, 2]", '{"7": "첫째 팀"}'])
def test_parse_assignment_rejects_unreadable_answers(text):
    with pytest.raises(AiBadResponse):
        parse_assignment(text)


def test_call_model_sends_expected_request_and_returns_text():
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return _ok("안녕")

    out = call_model("a/b:free", [{"role": "user", "content": "x"}], api_key="test-key", post=post)
    assert out == "안녕"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "a/b:free"
    assert seen["body"]["reasoning"] == {"effort": "low"}
    assert seen["body"]["max_tokens"] >= 4000


@pytest.mark.parametrize(
    "response, error",
    [
        (FakeResponse(429, {"error": {"message": "rate"}}), AiHttpError),
        (FakeResponse(500, text="boom"), AiHttpError),
        (FakeResponse(200, {"error": {"message": "upstream"}}), AiHttpError),
        (_ok(""), AiEmpty),
        (_ok("   \n"), AiEmpty),
        (_ok(None), AiEmpty),
        (FakeResponse(200, {"choices": []}), AiEmpty),
        (FakeResponse(200, text="<html>"), AiBadResponse),
    ],
)
def test_call_model_maps_bad_replies_to_specific_errors(response, error):
    with pytest.raises(error):
        call_model("m:free", [], api_key="k", post=lambda *a, **kw: response)


def test_call_model_http_error_mentions_status_code():
    with pytest.raises(AiHttpError) as err:
        call_model("m:free", [], api_key="k", post=lambda *a, **kw: FakeResponse(429, {}))
    assert "429" in str(err.value)


def test_call_model_maps_network_errors_and_never_leaks_the_key():
    def timeout_post(*a, **kw):
        raise requests.exceptions.ReadTimeout("slow")

    def broken_post(*a, **kw):
        raise requests.exceptions.ConnectionError("failed for SECRET-KEY-123")

    with pytest.raises(AiTimeout):
        call_model("m:free", [], api_key="SECRET-KEY-123", post=timeout_post)
    with pytest.raises(AiHttpError) as err:
        call_model("m:free", [], api_key="SECRET-KEY-123", post=broken_post)
    assert "SECRET-KEY-123" not in str(err.value)


def test_call_model_stops_at_deadline_even_if_the_server_never_answers():
    def slow_post(*a, **kw):
        time.sleep(2)
        return _ok("늦은 답")

    start = time.monotonic()
    with pytest.raises(AiTimeout):
        call_model("m:free", [], api_key="k", deadline_s=0.2, post=slow_post)
    assert time.monotonic() - start < 1.0


def _chain(replies, teams=EASY_TEAMS, left=EASY_LEFT):
    """replies: 모델 이름 -> 문자열(성공한 답) 또는 예외. 호출 기록과 상태 안내도 함께 돌려준다."""
    calls, statuses, attempts = [], [], []

    def call(model, messages):
        calls.append(model)
        reply = replies[model]
        if isinstance(reply, Exception):
            raise reply
        return reply

    result = run_chain(
        teams,
        left,
        models=list(replies),
        call=call,
        on_status=statuses.append,
        on_attempt=attempts.append,
    )
    return result, calls, statuses, attempts


def test_chain_falls_through_timeout_and_429_to_a_valid_third_answer():
    result, calls, _, attempts = _chain(
        {"m1": AiTimeout("120초 초과"), "m2": AiHttpError("HTTP 429"), "m3": GOOD}
    )
    assert result.source == "ai" and result.assignment == {"7": 1, "8": 2}
    assert calls == ["m1", "m2", "m3"]
    assert [a.model for a in attempts] == ["m1", "m2", "m3"]
    assert [a.status for a in attempts][-1] == "성공"
    assert all(a.error for a in attempts[:2])


def test_chain_rejects_unreadable_and_invalid_answers_then_accepts_valid():
    invalid = json.dumps({"7": 1})  # 8번 학생이 빠짐
    result, calls, _, attempts = _chain({"m1": "배정 못 함", "m2": invalid, "m3": GOOD})
    assert result.source == "ai" and calls == ["m1", "m2", "m3"]
    assert "빠진 학생" in attempts[1].error


def test_chain_stops_at_first_valid_answer():
    result, calls, _, _ = _chain({"m1": GOOD, "m2": GOOD, "m3": GOOD})
    assert result.source == "ai" and calls == ["m1"]


def test_chain_uses_solver_after_third_failure():
    failing = {"m1": AiEmpty("빈 응답"), "m2": AiTimeout("시간 초과"), "m3": "엉뚱한 말"}
    result, calls, statuses, attempts = _chain(failing)
    assert result.source == "solver" and calls == ["m1", "m2", "m3"]
    assert validate_assignment(result.assignment, EASY_TEAMS, EASY_LEFT) is None
    assert len(attempts) == 3
    assert "직접 배정" in statuses[-1]


def test_chain_skips_ai_when_infeasible():
    teams = {1: [student(1, "PL"), student(2, "TT")]}
    left = [student(3, "RW")]
    result, calls, statuses, _ = _chain({"m1": GOOD, "m2": GOOD, "m3": GOOD}, teams, left)
    assert calls == [] and result.source == "none" and result.assignment is None
    assert "웹 프로그래머" in result.note
    assert "웹 프로그래머" in statuses[-1]


def test_chain_with_nobody_to_assign_makes_no_calls():
    result, calls, _, _ = _chain({"m1": GOOD, "m2": GOOD, "m3": GOOD}, EASY_TEAMS, [])
    assert calls == [] and result.assignment == {}


def test_status_messages_name_the_model_tier_in_order():
    _, _, statuses, _ = _chain({"m1": AiEmpty("x"), "m2": AiEmpty("x"), "m3": AiEmpty("x")})
    waiting = [s for s in statuses if "응답 대기 중" in s]
    assert [("1차" in w, "2차" in w, "3차" in w) for w in waiting] == [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ]


# ---- 직접 배정 탐색이 한도를 넘어 포기한 경우 ----

def _solver_gives_up(monkeypatch):
    from assignment import SearchGaveUp

    def give_up(teams, unassigned, **kwargs):
        raise SearchGaveUp

    monkeypatch.setattr("ai_client.solve_assignment", give_up)


def test_chain_still_uses_a_valid_ai_answer_when_the_solver_gives_up(monkeypatch):
    _solver_gives_up(monkeypatch)
    result, calls, _, _ = _chain({"m1": GOOD, "m2": GOOD, "m3": GOOD})
    assert result.source == "ai" and result.assignment == {"7": 1, "8": 2}
    assert calls == ["m1"]


def test_chain_does_not_claim_infeasible_when_solver_gave_up_and_ai_failed(monkeypatch):
    _solver_gives_up(monkeypatch)
    failing = {"m1": AiEmpty("빈 응답"), "m2": AiTimeout("시간 초과"), "m3": "엉뚱한 말"}
    result, calls, statuses, _ = _chain(failing)
    assert calls == ["m1", "m2", "m3"]  # 배정이 없다고 단정하지 않으므로 AI는 끝까지 불러 본다
    assert result.source == "none" and result.assignment is None
    assert result.gave_up is True
    assert "찾지 못" in result.note and "배정이 없" not in result.note
    assert "오래 걸" in statuses[0]
