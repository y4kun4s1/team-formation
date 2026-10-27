import csv
import io

from models import ROLES, RosterRow

EXPECTED_COLUMNS = ["student_no", "name", "main_role", "sub_roles"]
MAX_SUB_ROLES = 2


class RosterError(ValueError):
    """명단 CSV에 문제가 있을 때 쓰는 예외. 메시지는 화면에 그대로 보여줘도 안전하다."""


def parse_roster(data: bytes) -> list[RosterRow]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RosterError("파일을 UTF-8로 읽을 수 없음") from exc

    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if header != EXPECTED_COLUMNS:
        raise RosterError(f"열 이름이 맞지 않음(필요한 열: {', '.join(EXPECTED_COLUMNS)})")

    rows: list[RosterRow] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(reader, start=2):
        if None in raw or any(v is None for v in raw.values()):
            raise RosterError(f"{line_no}번째 줄: 열 개수가 맞지 않음")
        student_no = raw["student_no"].strip()
        name = raw["name"].strip()
        main_role = raw["main_role"].strip()
        sub_roles = tuple(p.strip() for p in raw["sub_roles"].split(";") if p.strip())

        if not student_no or not name:
            raise RosterError(f"{line_no}번째 줄: 학번이나 이름이 비어 있음")
        if student_no == "admin":
            raise RosterError(f"{line_no}번째 줄: 학번으로 admin을 쓸 수 없음")
        if student_no in seen:
            raise RosterError(f"{line_no}번째 줄: 학번 {student_no}이 중복됨")
        if main_role not in ROLES:
            raise RosterError(f"{line_no}번째 줄: 알 수 없는 주 역할({main_role})")
        if len(sub_roles) > MAX_SUB_ROLES:
            raise RosterError(f"{line_no}번째 줄: 보조 역할은 {MAX_SUB_ROLES}개까지 가능함")
        for role in sub_roles:
            if role not in ROLES:
                raise RosterError(f"{line_no}번째 줄: 알 수 없는 보조 역할({role})")

        seen.add(student_no)
        rows.append(RosterRow(student_no, name, main_role, sub_roles))

    if not rows:
        raise RosterError("명단이 비어 있음")
    return rows
