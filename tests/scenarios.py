"""공고 부록의 연습용 시나리오 3개와, 단계를 서비스에 그대로 실행하는 도우미.

행동: ("apply", 학생, 팀, 기대 결과) / ("vote", 투표자, 지원자, 찬성 여부, 기대 결과) /
      ("close", 기대 결과). 학생 n은 명단 CSV의 n번째 학생이다. 기대 결과가 None이면 확인하지 않는다.
막힌 지원에 대한 뒤의 투표는 공고대로 건너뛴다.
"""

from pathlib import Path

from rules import team_problem

FIXTURES = Path(__file__).parent / "fixtures"
ADMIN_PASSWORD = "admin-pw-for-tests"

EASY = {
    "file": "쉬움.csv",
    "prefix": "20261",
    "teams": 3,
    "steps": [
        [("apply", 1, 1, "승인"), ("apply", 2, 2, "승인"), ("apply", 3, 3, "승인")],
        [("apply", 4, 1, "대기"), ("vote", 1, 4, True, "승인")],
        [("apply", 5, 2, "대기"), ("vote", 2, 5, True, "승인")],
        [("apply", 7, 1, "대기"), ("vote", 1, 7, True, "대기"), ("vote", 4, 7, False, "거절")],
        [("apply", 6, 3, "대기"), ("vote", 3, 6, True, "승인")],
        [("close", "승인")],
    ],
}

MID = {
    "file": "중간.csv",
    "prefix": "20262",
    "teams": 4,
    "steps": [
        [("apply", 1, 1, "승인")],
        [("apply", 5, 1, "대기"), ("vote", 1, 5, True, "승인")],
        [("apply", 2, 1, "막힘")],
        [("apply", 6, 2, "승인")],
        [("apply", 9, 2, "대기"), ("vote", 6, 9, True, "승인")],
        [("apply", 10, 2, "대기"), ("vote", 6, 10, True, "대기"), ("vote", 9, 10, False, "거절")],
        [("apply", 3, 3, "승인"), ("apply", 11, 4, "승인")],
        [("apply", 12, 4, "대기"), ("apply", 12, 3, "막힘")],
        [("close", "승인")],
        [("apply", 2, 3, "막힘")],
    ],
}

HARD = {
    "file": "어려움.csv",
    "prefix": "20263",
    "teams": 4,
    "steps": [
        [("apply", 1, 1, "승인")],
        [("apply", 4, 1, "대기"), ("vote", 1, 4, True, "승인")],
        [("apply", 7, 1, "대기"), ("vote", 1, 7, True, "대기"), ("vote", 4, 7, True, "승인")],
        [("apply", 2, 2, "승인")],
        [("apply", 8, 2, "대기"), ("vote", 2, 8, True, "승인")],
        [("apply", 5, 2, "대기"), ("apply", 6, 2, "대기")],
        [("vote", 2, 5, True, "대기"), ("vote", 8, 5, True, "승인")],
        [("vote", 2, 6, True, "대기"), ("vote", 8, 6, True, "대기"), ("vote", 5, 6, True, "거절")],
        [("apply", 11, 3, "승인")],
        [("apply", 15, 3, "대기"), ("vote", 11, 15, True, "승인")],
        [("apply", 14, 3, "막힘")],
        [("apply", 10, 4, "승인")],
        [("apply", 13, 1, "대기"), ("vote", 1, 13, True, "대기")],
        [("close", "승인")],
    ],
}

SCENARIOS = {"easy": EASY, "mid": MID, "hard": HARD}


def roster_bytes(scenario: dict) -> bytes:
    return (FIXTURES / scenario["file"]).read_bytes()


class Runner:
    def __init__(self, svc, scenario: dict, roster: bytes | None = None):
        self.svc = svc
        self.scenario = scenario
        self.roster = roster if roster is not None else roster_bytes(scenario)
        self.admin_id = svc.login("admin", ADMIN_PASSWORD).id
        self._ids: dict[int, int] = {}

    def setup(self) -> None:
        result = self.svc.upload_roster(self.admin_id, self.roster, self.scenario["teams"])
        assert result.kind == "승인", result.text

    def sid(self, n: int) -> int:
        if n not in self._ids:
            student = self.svc.login(f"{self.scenario['prefix']}{n:02d}", "1234")
            assert student is not None, f"학생{n} 로그인 실패"
            self._ids[n] = student.id
        return self._ids[n]

    def run(self, steps, start: int = 1) -> None:
        for number, step in enumerate(steps, start=start):
            for action in step:
                kind, *args = action
                if kind == "apply":
                    student, team, expected = args
                    result = self.svc.apply(self.sid(student), team)
                elif kind == "vote":
                    voter, applicant, yes, expected = args
                    application = self.svc.application_of(self.sid(applicant))
                    assert application is not None, f"{number}단계: 학생{applicant}의 대기 중인 지원이 없음"
                    result = self.svc.vote(self.sid(voter), application.id, yes)
                else:
                    (expected,) = args
                    result = self.svc.close(self.admin_id)
                if expected is not None:
                    assert result.kind == expected, f"{number}단계 {action}: {result}"
                    assert result.text.startswith(expected), result.text

    def finish(self) -> None:
        result, assignment = self.svc.propose_assignment(self.admin_id, lambda s: None)
        assert result.kind == "승인", result.text
        confirmed = self.svc.confirm(self.admin_id, assignment)
        assert confirmed.kind == "승인", confirmed.text
        assert_valid_final(self.svc)


def assert_valid_final(svc) -> None:
    assert svc.unassigned() == []
    for team_id, members in svc.teams().items():
        assert team_problem(members) is None, f"팀{team_id}: {team_problem(members)}"
