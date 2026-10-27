# 팀 편성 웹 앱

학생이 팀에 지원하고 투표하며, 관리자가 마감 후 AI로 남은 학생을 배정하는 Streamlit 앱입니다.

## 1. 무엇을 하는 앱인가

- 학생: 학번과 비밀번호(1234)로 로그인 → 팀 목록 확인 → 팀에 지원 → 우리 팀에 온 지원에 찬성/거부.
- 관리자(학번 `admin`): 명단 CSV와 팀 수로 시작 → 모집 마감 → AI 배정 → 배정안 확인 후 확정.
- 팀 규칙: 팀은 2~4명, 같은 주 역할 중복 없음, 팀마다 기획자와 웹 프로그래머가 서로 다른 사람으로 있어야 함.
- 화면은 3초마다 자동으로 갱신되어 다른 사람의 변경이 새로고침 없이 보입니다.

## 2. 구성

| 파일 | 역할 |
|---|---|
| `streamlit_app.py` | 화면과 화면 key |
| `service.py` | 지원·투표·마감·배정·확정 (DB 잠금으로 동시 요청 처리) |
| `rules.py`, `assignment.py` | 팀 규칙 검사, 직접 배정 풀이 |
| `ai_client.py` | OpenRouter 무료 모델 1·2·3차 호출(모델당 120초), 실패하면 앱이 직접 배정 |
| `schema.sql`, `db.py` | `tf_` 표와 연결 풀 (앱 시작 시 자동 생성) |
| `scripts/` | 제출 전 점검, zip 만들기, 모델 이름 확인 |
| `.github/workflows/keepalive.yml` | 하루 한 번 DB 질의로 Supabase 일시정지 방지 |

## 3. 설정값 (코드나 화면에 쓰지 않음)

`DATABASE_URL`(Supabase Session pooler 주소), `OPENROUTER_API_KEY`, `ADMIN_PASSWORD`.
로컬은 `key.env`(git 제외), 배포는 Streamlit Secrets, keepalive는 GitHub Secrets에 넣습니다.
선택: `AI_MODELS`(쉼표로 구분, 모두 `:free`)로 모델을 바꿀 수 있습니다.

## 4. 실행과 검사

```
pip install -r requirements.txt
streamlit run streamlit_app.py
pip install -r requirements-dev.txt && pytest      # 테스트용 로컬 PostgreSQL 필요
python scripts/audit.py                            # 제출 전 점검
python scripts/make_zip.py 학번                    # 제출용 zip
```

## 5. AI가 틀리거나 실패한 사례

> (직접 채우기) 배포 후 실제 무료 모델로 연습 시나리오를 돌리며 겪은 사례만 적습니다.
> 어떤 모델이, 어떤 입력에서, 어떻게 실패했고 앱이 어떻게 처리했는지(화면 캡처 근거 포함).

## 6. 막힌 점과 해결

> (직접 채우기) 실제로 막혔던 문제와 해결 과정.

## 7. 개선하고 싶은 점

> (직접 채우기) 직접 써 보고 느낀 개선점.
