"""제출 전 점검: 비밀값, 금지된 모델 이름, 표 이름, 화면 key를 검사한다.

사용: python scripts/audit.py   (문제가 있으면 목록을 보여주고 종료 코드 1)
찾은 비밀값은 화면에 출력하지 않고 파일 이름과 줄 번호만 보여준다.
"""

import re
import subprocess
import sys
from pathlib import Path

CODE_SUFFIXES = {".py", ".yml", ".yaml", ".toml", ".txt", ".sql", ".cfg", ".ini", ".json"}
SKIP_DIRS = ("tests/", "docs/")
GUARD_FILES = {"streamlit_app.py", "ai_client.py", "scripts/audit.py"}  # 금지 이름을 막는 코드라서 이름이 들어 있다
FIXED_KEYS = [
    "login_student_no", "login_password", "login_submit", "whoami", "result",
    "admin_roster_file", "admin_team_count", "admin_start", "admin_close", "unassigned",
    "admin_assign", "assign_status", "admin_confirm",
]
KEY_PREFIXES = ["team_", "apply_", "vote_yes_", "vote_no_"]
SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{8,}"),
    re.compile(r"postgres(?:ql)?://[^\s:/@]+:[^\s@]+@(?!127\.0\.0\.1|localhost)[^\s/]+"),
]
CREATE_TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_]\w*)", re.I)
KOREAN_KEY = re.compile(r"""\bkey\s*=\s*f?["'][^"']*[가-힣]""")


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def audit(root: Path, tracked: list[str] | None = None) -> list[str]:
    """문제 목록을 돌려준다. 비어 있으면 통과."""
    tracked = tracked if tracked is not None else tracked_files(root)
    problems: list[str] = []
    for rel in tracked:
        name = Path(rel).name
        if name == "key.env" or (name.endswith(".env") and name != ".env.example") or name == ".env":
            problems.append(f"{rel}: 비밀값 파일이 git에 올라가 있음")
    for rel in tracked:
        path = root / rel
        if path.suffix not in CODE_SUFFIXES or not path.is_file():
            continue
        if rel == "tests/test_audit.py":
            continue  # 이 점검 자체를 시험하려고 가짜 값을 일부러 넣은 파일
        text = path.read_text(encoding="utf-8", errors="ignore")
        for no, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in SECRET_PATTERNS):
                problems.append(f"{rel}:{no}: 키 또는 DB 주소처럼 보이는 값이 있음(값은 출력하지 않음)")
        if rel.startswith(SKIP_DIRS):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if rel not in GUARD_FILES and "openrouter/free" in line:
                problems.append(f"{rel}:{no}: openrouter/free는 쓰면 안 됨")
            if path.suffix in (".py", ".sql") and (m := CREATE_TABLE.search(line)) and not m.group(1).startswith("tf_"):
                problems.append(f"{rel}:{no}: 표 이름이 tf_로 시작하지 않음({m.group(1)})")
            if path.suffix == ".py" and KOREAN_KEY.search(line):
                problems.append(f"{rel}:{no}: 화면 key에 한글이 있음")
    app = root / "streamlit_app.py"
    if "streamlit_app.py" in tracked and app.is_file():
        text = app.read_text(encoding="utf-8")
        for k in FIXED_KEYS:
            if f'key="{k}"' not in text:
                problems.append(f"streamlit_app.py: 필수 key 없음: {k}")
        for p in KEY_PREFIXES:
            if f'key=f"{p}{{' not in text:
                problems.append(f"streamlit_app.py: 필수 key 없음: {p}…")
    else:
        problems.append("streamlit_app.py가 git에 없음")
    if (root / "key.env").exists() and "key.env" not in (root / ".gitignore").read_text(encoding="utf-8"):
        problems.append(".gitignore에 key.env가 없음")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    problems = audit(root)
    for p in problems:
        print("문제:", p)
    print("점검 통과" if not problems else f"{len(problems)}개 문제")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
