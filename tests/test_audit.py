import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from audit import audit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_repo_passes_audit():
    assert audit(ROOT) == []


def test_audit_flags_planted_problems(tmp_path):
    files = {
        ".env": "A=1\n",
        "key.env": "B=2\n",
        "bad.py": 'k = "sk-or-v1-abcdefghijkl"\nm = "openrouter/free"\nx = st.button("a", key="로그인")\n'
                  'u = "postgresql://u:pw@db.example.com:5432/x"\n',
        "bad.sql": "create table users (id int);\n",
        "streamlit_app.py": "pass\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    out = "\n".join(audit(tmp_path, list(files)))
    for expected in ("key.env: 비밀값", ".env: 비밀값", "키 또는 DB 주소", "openrouter/free", "tf_로 시작하지", "한글", "필수 key 없음", ".gitignore"):
        assert expected in out, expected
    assert "abcdefghijkl" not in out
