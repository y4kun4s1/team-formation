from pathlib import Path

import pytest

from csv_roster import RosterError, parse_roster

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = "student_no,name,main_role,sub_roles\n"


def _with_bom(text: str) -> bytes:
    return ("﻿" + text).encode("utf-8")


def test_parse_roster_accepts_bom_and_empty_sub_roles():
    data = _with_bom(HEADER + "2026102,가,웹 프로그래머,\n2026103,나,테스터,기획자;발표자\n")
    rows = parse_roster(data)
    assert rows[0].student_no == "2026102"
    assert rows[0].sub_roles == ()
    assert rows[1].sub_roles == ("기획자", "발표자")


def test_parse_roster_accepts_plain_utf8_crlf_and_strips_spaces():
    data = (HEADER + " 2026102 , 가 ,기획자,\n").replace("\n", "\r\n").encode("utf-8")
    rows = parse_roster(data)
    assert (rows[0].student_no, rows[0].name) == ("2026102", "가")


@pytest.mark.parametrize("name,count", [("쉬움", 8), ("중간", 12), ("어려움", 16)])
def test_parse_roster_reads_all_three_fixture_files(name, count):
    rows = parse_roster((FIXTURES / f"{name}.csv").read_bytes())
    assert len(rows) == count


@pytest.mark.parametrize(
    "text, expected_word",
    [
        ("student_no,name,role\n2026102,가,기획자\n", "열"),
        (HEADER + "2026102,가,기획자\n", "열"),
        (HEADER + "2026102,가,마법사,\n", "역할"),
        (HEADER + "2026102,가,기획자,마법사\n", "역할"),
        (HEADER + "2026102,가,기획자,테스터;발표자;보고서 작성자\n", "보조 역할"),
        (HEADER + "2026102,가,기획자,\n2026102,나,테스터,\n", "중복"),
        (HEADER + "admin,가,기획자,\n", "admin"),
        (HEADER, "비어"),
    ],
)
def test_parse_roster_rejects_bad_files_with_reason(text, expected_word):
    with pytest.raises(RosterError) as err:
        parse_roster(_with_bom(text))
    assert expected_word in str(err.value)


def test_parse_roster_rejects_non_utf8_bytes():
    with pytest.raises(RosterError) as err:
        parse_roster("가나다".encode("cp949"))
    assert "UTF-8" in str(err.value)
