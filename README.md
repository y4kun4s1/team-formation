# 팀 편성 웹 앱

학생이 팀에 지원하고 투표하며, 관리자가 마감한 뒤 AI로 남은 학생을 배정하는 Streamlit 앱입니다. (작성자: 정지수)

## 1. 배포 주소

> https://team-formation-lblktqqub8tmnm2o27z8g8.streamlit.app

## 2. PRD와 구현 계획의 위치

- PRD: `docs/PRD.md`
- 구현 계획: `docs/구현 계획.md`

## 3. 고른 해결 방법

| 기능 | 고른 해결 | 구현 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)`로 3초마다 바뀐 부분만 다시 그림 (`streamlit_app.py`의 `live_view`) |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원 단계에서, 기획자와 웹 프로그래머 자리를 채울 수 없게 되는 지원은 "막힘" (`rules.py`의 `check_apply`) |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 승인 직전에 규칙을 다시 검사하고 어기면 거절 (`service.py`의 `vote`) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | DB의 유일성 제약 `(team_id, slot_no)`와 팀 행 잠금 (`schema.sql`, `db.py`의 `insert_member`) |
| AI 응답이 늦을 때 | 어려운 해결 | 모델마다 응답 전체 120초를 재서 끊고 다음 모델로 넘어감 (`ai_client.py`의 `call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 검사 다섯 가지를 모두 통과하는 배정을 앱이 끝까지 찾음. 탐색이 2초를 넘으면 포기하고 AI의 답만 검사함 (`assignment.py`의 `solve_assignment`) |
| AI 대기 중 화면 | 어려운 해결 | 몇 차 모델의 응답을 기다리는 중인지 `assign_status`에 보여 줌 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions가 매일 DB에 질의를 보냄 (`.github/workflows/keepalive.yml`) |

## 4. 쓴 모델 3개

> (배포 전 `python scripts/check_models.py`로 확인한 뒤 실제로 쓴 이름으로 맞추기)

- 1차 `poolside/laguna-s-2.1:free`
- 2차 `nvidia/nemotron-3.5-lightning:free`
- 3차 `thinkingmachines/inkling:free`

모델은 환경변수 `AI_MODELS`(쉼표로 구분, 모두 `:free`)로 바꿀 수 있고, `openrouter/free`는 쓰지 않습니다.

## 5. 오픈라우터 AI 모델 호출 실패 사례

**관련 사례: AI를 부르기 전에 배정이 불가능하다고 판단한 경우 (모델 호출 실패는 아님)**

마감 뒤 관리자 화면에서 "AI 배정"을 눌렀더니 "규칙을 지키는 배정이 없음"이 나왔다. 앱이 AI를 부르기 전에 직접 풀이로 먼저 검사해서, 지금 상태로는 규칙(팀 인원, 주 역할 중복, 필수 포함 역할)을 모두 지키는 배정이 없다고 판단했기 때문이다. 이 경우 앱은 배정안을 만들지 않고 AI도 부르지 않는다(공고 2.5절 8항). 화면 아래의 "최근 AI 호출 기록"에 "아직 AI를 부른 기록이 없음"이 나오는 것이 AI를 부르지 않았다는 근거다. 그래서 이 사례는 모델이 실패한 것이 아니라 호출 전에 걸러진 것이다.

![AI 배정을 눌렀지만 규칙을 지키는 배정이 없어 AI를 부르지 않은 화면](img/rule-none.png)

**모델 호출 실패 사례**

> 실패사례는 발견하지 못함

## 6. 클로드가 잘못 추론하거나 답한 사례

클로드가 로그인 화면에 "학생 비밀번호는 모두 1234예요"라는 안내 문구를 넣었다. 비밀번호가 화면에 나오면 안 된다는 점을 놓친 것이다. 배포한 앱의 로그인 화면을 직접 보다가 알아챘고, 커밋 `6e6a8094ffe85289055bb82cc0bcb65856fda978`에서 그 문구를 지웠다. 지운 뒤에는 화면에 "1234"가 없는지 확인하는 테스트(`tests/test_ui_keys.py`의 `test_login_screen_hides_student_password`)를 추가했다.

- 알아챈 화면(문구가 보이는 로그인 화면): `img/password-hint.png`

![로그인 화면에 학생 비밀번호 안내가 나오는 모습](img/password-hint.png)

## 7. 제출한 앱에서 발견한 문제점과 개선할 점

**문제점: 로그인 화면에서 Enter 키를 눌러도 로그인되지 않음**

배포한 앱의 로그인 화면(모든 연습 시나리오의 첫 단계)에서 비밀번호 칸에 글자를 쓰면 칸 안에 "Press Enter to apply"라는 안내가 나온다. 그래서 Enter를 누르면 로그인될 것처럼 보이지만, 로그인 단추를 따로 눌러야만 로그인되었다. 입력칸이 폼으로 묶여 있지 않아서 Enter가 단추를 누르는 동작으로 이어지지 않은 것이다. 사용자가 이 화면을 보고 직접 알아챘다.

![비밀번호 칸에 Enter 안내가 나오지만 Enter로 로그인되지 않는 로그인 화면](img/enter-login.png)

편의성 개선: 로그인 단추뿐 아니라 Enter 키로도 로그인되도록 했다. 로그인 입력칸을 `st.form`으로 묶고 로그인 단추를 `st.form_submit_button`으로 바꿔 Enter로도 로그인되게 했다(커밋: `7590d104323f5b9e7e31b19e0942e90b7d7d1cbf`). 화면 key는 그대로다. Enter로 로그인되는지 확인하는 테스트(`tests/test_ui_keys.py`의 `test_enter_key_logs_in`)를 추가했다.

**문제점 2: "규칙을 지키는 배정이 없음"의 이유가 같은 말을 반복하고 구체적이지 않음**

AI 배정이 불가능할 때 관리자 화면에 "규칙을 지키는 배정이 없음: 규칙을 지키는 배정이 없음(팀 인원과 역할 조건을 한꺼번에 만족하는 조합이 없음)"이 나왔다. 앞의 문구와 이유의 첫머리가 같은 말이라 중복되고, 이유도 어느 팀의 어떤 역할이 모자란지 알려 주지 않는다. 공고 2.5절 8항은 "팀3에 웹 프로그래머를 맡을 학생이 없음"처럼 구체적인 이유를 예로 든다.

![같은 말이 반복되고 이유가 일반적인 "규칙을 지키는 배정이 없음" 화면](img/rule-none.png)

개선: 이유 문장에서 반복되던 앞부분을 지웠다(커밋: `7590d104323f5b9e7e31b19e0942e90b7d7d1cbf`). 일반적인 이유 문장은 "남은 학생을 어떻게 나눠 넣어도 팀 인원, 주 역할 중복, 필수 포함 역할 중 하나를 어기게 됨"으로 바꿨다. 어느 팀의 어떤 역할이 모자란지는 이유가 특정되는 경우에 구체적으로 알려 준다(예: "팀3에 웹 프로그래머를 맡을 학생이 없음"). 문제점 3을 고치면서(커밋: `7c6f91dbd229f874e056bea3db5d6d2881bc5889`) 주 역할이 같은 학생이 팀 수보다 많은 경우, 필수 포함 역할 자리를 채우면 팀이 4명을 넘는 경우, 필수 포함 역할을 맡을 수 있는 학생이 모자란 경우의 이유 문장도 추가했다. 이런 진단에 걸리지 않는 복잡한 조합은 여전히 일반적인 문장으로 남는다.

**문제점 3: 직접 배정이 일부 입력에서 끝나지 않고 오래 걸림**

AI를 부르기 전에 앱이 먼저 "규칙을 지키는 배정이 있는지" 직접 찾는데(`assignment.py`의 `solve_assignment`), 배정이 없는 입력에서 너무 오래 걸렸다. 이 문제는 배포한 앱의 화면에서 본 것이 아니라, 코드를 검토하면서 직접 배정의 속도를 재 보다가 알아챘다. 그래서 문제를 보여 주는 화면 캡처는 없고, 대신 잰 숫자를 적는다.

- 웹 프로그래머를 맡을 수 있는 학생이 한 명도 없는 입력(학생 16명, 팀 4개)에서 8초가 넘어도 끝나지 않았다. 이때 관리자 화면에서 "AI 배정"을 누르면 화면이 오래 멈춘다.
- 이 입력에서 탐색 횟수는 학생이 한 명 늘 때마다 대략 두 배가 되었다(학생 6명 12회, 8명 120회, 10명 2,505회, 12명 10,533회).
- 배정이 있는 입력도 느린 경우가 있었다(학생 36명, 팀 9개에서 약 2.1초).
- 원인은 탐색을 일찍 끊는 검사가 "필요한 인원 수"만 세고, 남은 학생 중에 그 역할을 맡을 수 있는 사람이 있는지는 보지 않았기 때문이다. 그래서 불가능하다는 것이 확실한 입력도 끝까지 뒤졌다. 불가능할 때 이유를 만드는 `explain_infeasible`도 같은 탐색을 한 번 더 돌렸다.

개선(커밋: `7c6f91dbd229f874e056bea3db5d6d2881bc5889`):

- 탐색 중에 불가능이 확실한 경우를 바로 끊는 검사를 늘렸다. 필수 포함 역할을 맡을 수 있는 남은 학생 수, 같은 주 역할의 학생 수와 들어갈 수 있는 팀 수, 팀별 남은 자리를 본다.
- 갈 수 있는 팀이 가장 적은 학생부터 놓도록 바꾸고, 한곳에서 오래 헤매면 순서를 바꿔 다시 시작하게 했다.
- `explain_infeasible`은 탐색 전에 이런 진단을 먼저 하게 했다.
- 탐색이 2초를 넘으면 "배정이 없다"고 단정하지 않고 포기한다. 이때는 AI의 답을 규칙으로 검사해서 쓰고, AI도 실패하면 "배정을 찾지 못함"이라고 알려 준다(`ai_client.py`의 `run_chain`, `service.py`의 `propose_assignment`).

확인한 내용:

- 같은 입력으로 다시 재면 위 16명 입력은 0.0ms, 36명 입력은 약 8ms로 줄었다.
- 정확한 풀이기(OR-Tools)를 기준값으로 삼아 무작위 입력 1,400건에서 "배정이 있다/없다" 판정을 비교했더니 다른 경우가 없었다. 수정 전 코드와는 작은 무작위 입력 20,000건을 비교했고 역시 다른 경우가 없었다. 이 입력들은 내가 만든 가짜 입력이고 실제 채점 시나리오가 아니다.
- 같은 가짜 입력 1,400건 중 1건은 2초 안에 못 풀어 탐색을 포기했다. 이 경우는 위 포기 처리로 넘어간다.
- 추가한 테스트: `tests/test_assignment.py`의 `test_no_web_programmer_anywhere_is_rejected_quickly`, `test_more_students_with_the_same_main_role_than_teams_is_rejected_quickly`, `test_larger_feasible_roster_is_found_quickly` 등, `tests/test_ai_client.py`의 `test_chain_does_not_claim_infeasible_when_solver_gave_up_and_ai_failed`, `tests/test_service.py`의 `test_solver_giving_up_is_reported_without_claiming_there_is_no_assignment`.
- 이 수정은 클로드와 함께 했고, 클로드가 속도를 재서 문제를 찾았다.

---

## 참고: 구성과 실행

| 파일 | 역할 |
|---|---|
| `streamlit_app.py` | 화면과 화면 key |
| `service.py` | 지원·투표·마감·배정·확정 |
| `rules.py`, `assignment.py` | 팀 규칙 검사, 직접 배정 풀이 |
| `ai_client.py` | OpenRouter 무료 모델 1·2·3차 호출 |
| `schema.sql`, `db.py` | `tf_` 표와 연결 풀 (앱 시작 시 자동 생성) |
| `scripts/` | 제출 전 점검, zip 만들기, 모델 이름 확인 |

설정값(`DATABASE_URL`, `OPENROUTER_API_KEY`, `ADMIN_PASSWORD`)은 코드에 적지 않고 Streamlit Secrets와 GitHub Secrets에만 넣습니다.

```
pip install -r requirements.txt
streamlit run streamlit_app.py
python scripts/audit.py          # 제출 전 점검
python scripts/make_zip.py 학번   # 제출용 zip
```
