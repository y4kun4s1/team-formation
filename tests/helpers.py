from models import Student

ABBR = {
    "PL": "기획자",
    "WP": "웹 프로그래머",
    "FD": "프론트엔드 디자이너",
    "TT": "테스터",
    "RW": "보고서 작성자",
    "PT": "발표자",
}


def student(n: int, main: str, subs: str = "") -> Student:
    """student(7, "FD", "PT;TT") -> 학번 7, 주 역할 프론트엔드 디자이너, 보조 발표자와 테스터."""
    sub_roles = tuple(ABBR[s] for s in subs.split(";") if s)
    return Student(id=n, student_no=str(n), name=f"학생{n}", main_role=ABBR[main], sub_roles=sub_roles)


# 쉬움 시나리오 6단계 직전 상태: 팀 3개와 팀이 없는 학생 2명
EASY_TEAMS = {
    1: [student(1, "PL", "RW"), student(4, "WP", "FD")],
    2: [student(2, "PL", "PT"), student(5, "WP")],
    3: [student(3, "PL", "TT"), student(6, "WP", "TT")],
}
EASY_LEFT = [student(7, "FD", "PT"), student(8, "TT", "RW;PT")]
