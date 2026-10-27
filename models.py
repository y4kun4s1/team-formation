from dataclasses import dataclass

ROLES: tuple[str, ...] = (
    "기획자",
    "웹 프로그래머",
    "프론트엔드 디자이너",
    "테스터",
    "보고서 작성자",
    "발표자",
)

# 모든 팀에 서로 다른 사람으로 있어야 하는 역할
REQUIRED_ROLES: tuple[str, ...] = ("기획자", "웹 프로그래머")


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...]
    is_admin: bool = False


@dataclass(frozen=True)
class RosterRow:
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...]
