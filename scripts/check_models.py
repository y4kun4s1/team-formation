"""무료 모델 이름이 OpenRouter에 실제로 있는지 확인한다. 키는 key.env나 환경변수에서 읽고 출력하지 않는다.

사용: python scripts/check_models.py          (모델 목록만 확인, 요청 한도를 쓰지 않음)
      python scripts/check_models.py --call   (첫 모델에 짧은 질문 1회, 하루 한도 1회 사용)
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
load_dotenv(HERE / ".env")
load_dotenv(HERE / "key.env")

from ai_client import DEFAULT_MODELS  # noqa: E402


def main() -> int:
    raw = os.environ.get("AI_MODELS")
    models = tuple(m.strip() for m in raw.split(",") if m.strip()) if raw else DEFAULT_MODELS
    key = os.environ.get("OPENROUTER_API_KEY")
    resp = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
    resp.raise_for_status()
    available = {m["id"] for m in resp.json().get("data", [])}
    bad = 0
    for m in models:
        ok = m in available and m.endswith(":free")
        print(("있음 " if ok else "없음 ") + m)
        bad += not ok
    if bad:
        print("없는 모델은 환경변수 AI_MODELS(쉼표로 구분, 모두 :free)로 바꾸세요.")
    if "--call" in sys.argv:
        if not key:
            print("OPENROUTER_API_KEY가 없어 호출 시험을 건너뜀")
            return 1
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": models[0], "messages": [{"role": "user", "content": "OK라고만 답해"}], "max_tokens": 200},
            timeout=120,
        )
        print("호출 결과 코드:", r.status_code)
        if r.status_code != 200:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
