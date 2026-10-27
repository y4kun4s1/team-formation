"""제출용 zip을 만든다. git에 올라간 파일만 넣으므로 key.env, .env, .venv는 들어가지 않는다.

사용: python scripts/make_zip.py 학번
"""

import subprocess
import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("사용법: python scripts/make_zip.py 학번")
        return 2
    root = Path(__file__).resolve().parent.parent
    names = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    bad = [n for n in names if Path(n).name in (".env", "key.env") or n.startswith(".venv/")]
    if bad:
        print("git에 올라가면 안 되는 파일이 있어 중단:", ", ".join(bad))
        return 1
    out = root / f"{sys.argv[1].strip()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(root / n, n)
    print(f"만들었음: {out.name} ({len(names)}개 파일)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
