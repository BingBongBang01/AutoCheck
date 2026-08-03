# CHANGELOG

버전 규칙: 수정할 때마다 마지막 자리 1씩 증가. 인사평가 KPI 계획상 v1.0.0은 8월(v1.0 단계,
"핵심 기능 + 전체 완성") 전까지 올리지 않음 — 지금은 전부 0.0.x 범위.

**번호 체계 통일(v0.0.025부터)** — 그동안 폴더명(`V0/lab_grader_V0.0.025`)과 `VERSION` 파일은
3자리(`0.0.025`)를, CHANGELOG는 2자리(`v0.0.15`)를 써서 같은 릴리스가 두 개의 번호로 불렸다.
이제 **3자리(`0.0.0NN`)로 통일**하고 폴더명 = `VERSION` = CHANGELOG 제목이 항상 같은 값을 쓴다.
UI 좌하단에 표시되는 값도 `VERSION` 파일을 그대로 읽은 것이라 자동으로 일치한다.
아래 v0.0.15 이하의 과거 항목은 당시 기록이라 번호를 손대지 않았다 (v0.0.15 = 폴더 V0.0.024).

## v0.5.0 (현재) — AutoCheck v0.5.0 마이그레이션 · 프로젝트 구조 및 문서 링크 정체성 업데이트

- **버전 동기화 및 경로 정규화 (`VERSION`, `README.md`, `CHANGELOG.md`)**
  - 폴더명(`V0.5.0`)과 애플리케이션 버전(`VERSION`)을 `0.5.0`으로 상향 및 통합 동기화
  - `README.md` 내 절대 파일 링크(`file:///...`)를 상대 경로 마크다운 링크로 정규화
  - 작업 범위 및 하위 모듈이 `V0.5.0` 디렉토리 기준으로 독립 동작하도록 전용 환경 설정 정리

## v0.0.036 — 시스템 모듈화 · I/O 로깅 성능 최적화 · vEOS-lab AI 분석 개편 · UI 로그 스트리밍 · MD3 앱 아이콘 적용

폴더명 = `VERSION` = CHANGELOG 제목을 같은 값으로 유지하는 규칙에 따라 이 버전의 변경 사항을 기록합니다.

### 시스템 최적화 및 모듈화 (v0.0.036)
- **비동기 로깅 인프라 구축 (`core/app_logger.py`)**
  - 디스크 쓰기 병목 방지를 위해 `queue.Queue` 기반 비동기 데몬 쓰기 루프(`_async_log_writer_loop`) 구현
  - `DEBUG` 수준을 포함한 모든 시스템/원시 로그를 `app_full_<timestamp>.log`에 비동기 수집하며, `atexit` 종료 처리 보장
- **UI 콘솔 필터링 & Delta 스트리밍 API (`api/logs_api.py`)**
  - UI 메모리 버퍼(`_buffer`)는 `INFO` 이상 로그만 보유하여 렌더링 부하 방지
  - IPC 페이로드 폭증을 막기 위해 `since_index` 기반 델타 로그 반환 및 1회 최대 500줄 IPC 속도제어 제한 적용
  - 세션 전체 로그 파일 분할 읽기 API (`get_full_log_chunk`) 도입
- **UI 로그 렌더링 200ms DOM Throttling & 영속 필터 (`web_ui/js/logs.js`)**
  - DOM 렌더링 병목 방지를 위해 200ms 속도제어(Throttling) 및 최대 500줄 DOM 제한 구현
  - 일시정지/재개 버튼 및 레벨 필터링 (`INFO`/`WARN`/`ERROR`/`DEBUG`) `localStorage` 영속화 적용
- **Arista vEOS-lab 가상 플랫폼 AI 분석 개편 (`ai_analysis/raw_log_analyzer.py`)**
  - vEOS 가상 환경 제약조건(하드웨어/파워/온도 센서 미지원 커맨드) 자동 무시 및 실제 운용 장애(Reload, Interface/MLAG down, BGP/EVPN, STP)에 집중하도록 프롬프트 강화
- **Local AI 파이프라인 경량화 & 파라미터 최적화 (`api/log_analysis_run_api.py`, `ai_config.yaml`)**
  - Local AI 호출 시 사전 필터링된 RuleCheck 텍스트를 구조화된 키-값 템플릿으로 전달하여 토큰 페이로드 축소
  - `ai_config.yaml` 내 `local_batching` 수치 조정 (`batch_chars: 2000`, `batch_segs: 2`, `max_tokens: 500`)으로 NPU 메모리 절감 및 타임아웃/500 오류 예방
- **탐색 캐시 모듈 도입 (`WorkspaceContextCache`)**
  - UI 탭 이동 및 렌더링 시 반복되던 고객사/프로파일 디렉터리 I/O 풀트리 스캔을 방지하기 위해 캐싱 구조 도입
  - 고객사/프로파일 생성, 수정, 삭제 CRUD 시 자동으로 캐시를 무효화(Invalidate)하도록 연동
- **탭 렌더링 교착 상태(Deadlock) 수정**
  - 캐시 모듈의 순환 호출 시 스레드가 차단되던 락 결함을 `threading.RLock()`으로 전환하여 탭이 로딩되지 않던 회귀 현상 해결
- **AI 서비스 프로바이더 캡슐화 (`core/ai/`)**
  - Cloud AI (Gemini) 및 Local LLM (Ollama) 프로바이더 추상화 및 `AIService` 싱글톤으로 인터페이스 통합
- **리포트 엔진 책임 분리 (`engine/report/`)**
  - 데이터 집계(`ReportDataAggregator`), 서식 변환(`ReportRenderer`), 내보내기(`ReportExporter`) 모듈로 구조 분리
- **공통 날짜/시간 유틸리티 모듈화 (`core/utils/datetime.py`)**
  - 전 시스템에 흩어져 있던 타임스탬프 포맷팅(`format_run_id`, `format_iso`) 일원화
- **Material Design 3 앱 아이콘 및 파비콘 적용**
  - 라우터/스위치 노드와 체크마크가 연상되는 MD3 스타일 아이콘 생성 및 파비콘, 탑바 로고에 적용

## v0.0.035 — 랩 데이터 정의 호환성 및 마이그레이션 안정화

## v0.0.034 — 시작 최적화 · 삭제된 로그 재등장 수정 · 드래그 자동 스크롤 · 중지 시 자동 저장 · 클라우드 AI 제공자 확장

### 중지 버튼 회귀 수정 · 제공자/모델 직접입력

**세션 터미널 '중지'가 동작하지 않던 문제 (직전 변경이 만든 회귀)**
- `stop_terminal_inspection()`에서 저장/폐기용 `discard` 인자를 없앴는데 JS 쪽 호출에 남아있던
  인자를 지우지 않았다(`call('stop_terminal_inspection', false)`). pywebview 브리지에서
  `TypeError: takes 1 positional argument but 2 were given`이 나면서 중지 요청이 서버에 도달하지
  못했다 — 화면에서는 '중지를 눌러도 아무 일도 안 일어남'으로 보였다. 인자 제거로 수정.
  (`connection-inspection.js`에 인자 추가 금지 주석을 남겼다.)
- 중지 버튼에 로딩 표시를 넣고, 이미 끝난 점검에 중지를 누르면 `alert` 대신 상태 문구로 안내하고
  버튼 상태를 정상으로 되돌린다.
- **점검시작 직후 중지가 최대 2초 늦게 듣던 문제도 수정** — 커맨드 전송 전 잔여 출력 드레인
  (`_wait_for_settled_output`, 최대 2s)을 통째로 기다려서 그 사이 중지가 무시됐다.
  `_drain_before_send()`가 0.25s씩 잘라 기다리며 중지를 확인한다(실측 2.0s → 0.55s).

**클라우드 API — 제공자·모델 '직접입력'**
- 모델 드롭다운 맨 끝에 **`직접입력…`** 추가. 고르면 옆에 텍스트 칸이 나타나 임의의 모델 ID를
  넣을 수 있다. 제공자들이 모델을 수시로 추가하는데 표에 없다고 막으면 새 모델을 못 쓰기 때문에,
  서버 쪽 모델 화이트리스트 검증도 풀었다. 저장된 모델이 목록에 없으면 다시 열었을 때도
  자동으로 직접입력 상태로 보여준다(`model_is_custom`).
- 제공자 드롭다운 맨 끝에 **`직접입력 (OpenAI 호환)`** 추가. 고르면 **API 주소(엔드포인트)** 칸이
  나타난다 — OpenRouter/Together/Fireworks/사내 게이트웨이/vLLM·LM Studio 같은 OpenAI 호환
  서비스를 코드 수정 없이 붙일 수 있다. 주소는 `http(s)://` 검사 후 항목에 저장되고, 분석 쪽
  핸들러가 제공자 표보다 항목의 `endpoint`를 먼저 보므로 **실제 호출도 그 주소로 나간다**
  (연결 테스트도 이 주소를 쓴다).
- 직접입력 제공자는 주소·모델이 없으면 저장을 거부하고, 일반 제공자로 되돌리면 주소가 남지 않는다.

### 삭제된 로그 · 목록/드래그 · 중지 저장 · 클라우드 제공자

**이미 삭제한 로그가 목록에 다시 나타나던 문제**
- 점검 1회는 `run_terminal_inspection()`이 **두 곳**에 같은 파일명으로 저장한다
  (`00_orignal_log/`와 `terminal_sessions/` — 보고서·대시보드가 후자를 읽어서 둘 다 필요).
  `list_log_files()`는 "00_orignal_log에 같은 이름이 있으면 terminal_sessions 쪽을 숨긴다"는
  **단방향** 규칙이었고, `delete_log_files()`도 사본을 한 방향으로 1개만 지웠다. 그래서
  00_orignal_log 사본만 지워지면 숨겨 주던 근거가 사라져서, 삭제한 로그가 '세션 터미널 점검'
  행으로 목록에 **다시 나타났다**(디스크에는 장비당 1개인데 목록엔 과거 로그까지 보이던 증상).
- 이제 목록은 **파일명으로 묶어** 1행으로 만들고 사본 경로를 전부 `paths`에 담는다. 삭제는
  `_log_copy_dirs()`가 알려주는 모든 디렉터리의 같은 이름을 **전부** 지운다 — 목록과 삭제가
  같은 기준(`_log_copy_dirs`)을 공유하므로 다시 어긋날 수 없다.
- 사본만 남은 잔해도 1행으로 보이고 정상 삭제된다. 탐색기에서 먼저 지운 파일을 삭제 요청해도
  에러를 띄우지 않는다.

**드래그로 범위선택할 때 자동 스크롤** (점검 로그 / 원본로그분석 / 마스킹 목록 + 세션 터미널 장비 목록)
- `core.js`에 공용 `createDragRangeSelect()` 추가 — 목록 4곳이 같은 구현을 쓴다.
- 가장자리 자동 스크롤만으로는 부족했다: 스크롤 중에는 커서가 멈춰 있어 `mousemove`가 안 오므로
  매 프레임 `elementFromPoint()`로 커서 밑 행을 다시 판정해 선택 범위를 이어서 넓힌다.
  (이게 없으면 스크롤만 되고 선택은 화면 끝 행에서 멈춘다.)
- **리스너 누수도 같이 수정** — `mousemove`/`mouseup`을 드래그 중에만 걸고 끝나면 떼어낸다.
  예전엔 목록을 다시 그릴 때마다(클릭 한 번에 `renderLogViewer()` 호출) document 리스너가 쌓였다.

**세션 터미널 — 중지하면 바로 저장**
- 저장/폐기를 묻던 `prompt()` 제거. 중지 버튼 = 지금까지 수집된 것을 그대로 저장.
  폐기 경로(`discard`)는 아예 없앴다 — 지우고 싶으면 '점검 로그' 탭에서 삭제하면 되고,
  그쪽이 되돌릴 수 있는 방향이다.
- 시작 전에 중지된 세션과 수집 출력이 빈 세션은 **저장하지 않는다** — 본문이 중단 표시 한 줄뿐인
  빈 로그가 목록에 쌓이는 걸 막는다(전송 실패는 원인을 남겨야 하므로 저장).

**클라우드 API — 제공자 3개 → 10개, 제공자별 모델 목록**
- 추가: **NVIDIA NIM**, xAI(Grok), Mistral, DeepSeek, Groq, Perplexity, Upstage(Solar, 한국어).
  기존 Anthropic/OpenAI/Gemini는 모델 ID를 현재 것으로 갱신(예전 목록의 `gemini-3.x-*`는
  실재하지 않는 ID였다).
- 제공자 표를 **`core/cloud_providers.py`** 한 곳으로 모았다 — 환경설정 화면, 연결 테스트,
  `findings_analyzer`, `raw_log_analyzer`가 같은 표를 읽는다. 제공자/모델 추가는 이 파일만 고치면 된다.
- 분석 호출은 제공자 id가 아니라 **호출 형식(wire)** 으로 핸들러를 고른다
  (`anthropic` / `gemini` / `openai_compat`). 새로 추가한 7곳은 전부 OpenAI 호환이라 기존
  핸들러를 그대로 쓰고 주소만 표에서 가져온다 — UI에만 추가돼서 실제로는 호출되지 않는
  '껍데기 제공자'가 생기지 않는다.
- **입력 필드 순서 통일** — 저장된 행과 추가 행이 모두 `[제공자] [이름] [모델] [API 키]`.
  예전엔 저장된 행이 이름→제공자→모델, 추가 행이 제공자→이름(모델 없음)이라 같은 값을
  다른 자리에서 입력해야 했다.
- **추가할 때도 모델 선택** — 제공자를 바꾸면 그 제공자가 지원하는 모델로 드롭다운이 다시 채워진다.
  제공자를 바꾸면 이전 제공자의 모델 ID가 남지 않게 새 제공자 기본 모델로 교체하고, 지원하지 않는
  모델은 저장을 거부한다.
- `batch_chars`/`max_tokens` 기본값도 제공자 표에서 나온다 — 모델별로 다르게 둘 때만
  `MODEL_PARAM_DEFAULTS`에 적는다.

### 시작 시 글자 깨짐 제거 · 시작 속도 최적화 · 죽은 파일 정리

**시작 직후 아이콘이 영어 단어로 보이던 문제 (글자 깨짐)**
- 원인: `web_ui/index.html`이 Google Fonts CDN(`fonts.googleapis.com`)에서 Material Symbols
  아이콘 폰트를 링크했다. 폰트가 도착하기 전까지는 `<span class="material-symbols-rounded">business</span>`
  같은 마크업이 **리거처 원문 그대로**("business", "dashboard", "folder_managed" …) 렌더링된다.
  창이 뜬 직후 사이드바·상단바가 전부 영어 단어로 보였고, 사내망/오프라인에서는 그 상태가 영구적이었다.
- 폰트 woff2(361KB, 정적 인스턴스)를 `web_ui/vendor/material-symbols/`에 번들하고 CDN 링크를 제거.
  `style.css`에 `@font-face`(+ `.material-symbols-rounded` 기본 규칙)를 직접 정의하고
  **`font-display: block`** — 폰트 준비 전에는 리거처 원문을 그리지 않고 자리만 비워 둔다.
  가변축(opsz/wght/FILL/GRAD)은 코드에서 쓰는 곳이 없어서 정적 인스턴스로 받았다(5.3MB → 361KB).
- 이제 시작할 때 **외부 네트워크 요청이 0건**이다.

**시작 속도**
- xterm(`xterm.js` 490KB + addon + css)을 `index.html`에서 미리 받지 않는다 —
  `connection-terminal.js`의 `ensureXterm()`이 '세션 터미널' 페이지를 **처음 열 때만** 주입한다.
  시작 시 기본 진입 페이지는 '환경 설정'이라 터미널을 안 쓰는 실행에서는 비용이 아예 없다.
- `refreshStatusBar()`의 브리지 호출 5개(`get_app_version`/`get_active_project`/`list_projects`/
  `get_customer_context`/`get_customer_profiles`)는 서로 의존이 없는데 하나씩 `await`해서
  왕복 5번을 순서대로 기다렸다 → `Promise.all`로 한 번에.

**죽은 파일 제거** (사이드바에서 이미 빠진 탭들의 잔해 — 라우터에서도 닿을 수 없었다)
- web_ui/js: `analysis.js` `architecture.js` `findings.js` `history.js` `reports.js`
  `discovery.js` `inspection.js` — `index.html`의 `<script>` 태그와 `core-navigation.js`
  라우터 항목도 같이 정리.
- api: `analysis_api.py` `discovery_api.py` `inspection_api.py` `misc_api.py`
  (Findings/History/Architecture mixin) — `main.py`의 `Api` 합성 목록에서 제거.
  `unl_parser.py`(EVE-NG .unl 파서)는 `discovery_api.py`만 쓰던 모듈이라 함께 제거.
- 아무 데서도 import되지 않던 모듈: `core/parsed_device.py` `engine/snapshots.py`
  `parsers/config_diff.py`.
- 한 번 쓰고 남은 산출물: `PHASE3/4/5_MIGRATION_REPORT.json` `migration_report.json`
  `search_results.txt`, 그리고 전체 `__pycache__/`.

## v0.0.030 — 탭 재배치 · 회차 간 장비목록 복사 · 자동 연결 확인 · 진입점 일원화

**탭 재배치 — 실제 사용 순서대로 4단계 그룹**
- `준비`(워크스페이스 → 장비 목록 → EVE 구성 불러오기 → 명령어 카탈로그 → 점검 항목)
  `실행`(세션 터미널 → 수집/채점 → 점검 로그) `결과`(대시보드 → 발견사항 → 분석 → 보고서 → 이력)
  `기타`(지식베이스 → 환경 설정 → 전체 로그 → 아키텍처). 사이드바에 그룹 라벨을 넣고,
  접었을 때는 라벨이 아이콘 폭을 넘치므로 얇은 구분선으로 바뀐다.
- **워크스페이스를 맨 위로** — 고객사/회차를 먼저 골라야 나머지 탭이 전부 그 기준으로 동작하는데
  6번째에 있었다. 반대로 **환경 설정은 아래로** — 전역 설정이라 처음 한 번 말고는 거의 안 건드린다.
- **점검 로그를 독립 탭으로 분리**(`web_ui/js/inspection-log.js`, 신규) — 원본/이상탐지/마스킹
  3종이 '수집/채점' 탭 안 카드에 파묻혀 있었다. 로그를 읽는 일은 채점을 돌리는 일과 목적이
  다르고, 채점 없이 세션 터미널로 모은 로그만 보는 경우도 많다.
- `점검 항목 설정` → `점검 항목`으로 이름 정정 — 실제로는 읽기 전용 뷰인데 '설정'이라
  편집할 수 있는 것처럼 보였다. 설명줄에 읽기 전용임과 커맨드는 카탈로그 탭에서 켠다고 명시.

**환경 설정이 정말 전역인지 확인 → 맞음. 다만 그 보장이 깨져 있어 수정**
- 확인 결과 환경 설정 탭의 모든 값(AI 우선순위·API 키·로컬 모델·배치·터미널 우클릭)은
  이미 앱 전역이다. 고객사/프로파일별로 갈라지는 값은 하나도 없다.
- 그런데 네 파일 전부 **CWD 상대경로 리터럴**이었다(`"ai_config.yaml"` 등). 앱을 다른
  디렉터리에서 실행하거나 exe로 패키징하면 CWD가 달라져 설정이 전부 기본값으로 되돌아간 것처럼
  보이고, 저장하면 엉뚱한 폴더에 새 파일이 생긴다 — `project_manager`는 이미 `AppPaths`로
  옮겨져 있었는데 환경 설정만 남아 있었다. `core/app_settings.py`(신규)로 경로를 모으고
  전부 `AppPaths` 기준으로 고정. `engine/collector.py`의 `connection_path` 기본값도 같은 문제라 함께 수정.
- 설정 탭 상단이 "프로젝트: X"라고 떠서 프로젝트별 설정처럼 보이던 것을 "모든 고객사·모든
  회차에 공통 적용"이라고 명시하도록 교체.

**같이 고친 버그**
- **[치명] 저장 도중 앱이 죽으면 장비목록이 소리 없이 잘려나갔다** (`core/atomic_io.py`, 신규)
  — 설정 YAML 저장이 전부 `open(path, "w")` 방식이었다. 여는 순간 파일이 0바이트가 되고
  그 다음에 내용을 채우기 때문에, 그 사이에 창을 닫거나 프로세스가 죽으면 파일이 잘린 채 남는다.
  하필 잘린 YAML도 문법상 멀쩡해서 오류 없이 그냥 로드된다 — 40대짜리 장비목록이 13대로
  줄어든 걸 사용자는 나중에야 발견하게 된다(실제로 재현해서 확인). `device_inventory.yaml`은
  타이핑 중 0.8초마다, 연결 확인 결과가 올 때마다 다시 쓰이는 앱에서 가장 자주 쓰이는 파일이라
  노출 시간이 상시였다. 임시 파일에 다 쓰고 `os.replace()`로 교체하는 방식으로 바꿔서,
  중단되면 "원본 그대로" 아니면 "새 내용 전부" 둘 중 하나만 남는다.
  `core/storage_service.py`와 `engine/profile_manager.py`는 이미 같은 방식을 쓰고 있었고
  (v0.0.15에서 같은 이유로 전환) 프로젝트 설정 YAML 18곳만 빠져 있었다 — 전부 전환:
  장비목록·커맨드 카탈로그·프로젝트/프로파일 meta·활성 프로젝트·AI 설정·API 키·SSH 설정·터미널 설정.
- **[치명] 보고서·점검 로그가 실행 위치에 따라 데이터를 못 찾았다** — `api/report_api.py`,
  `api/log_file_browser_api.py`, `api/terminal_inspection_api.py`가 로그 경로를
  `os.path.join("labs", project_id, "terminal_sessions")` / `"raw_logs"` 처럼 CWD 상대로
  조립하고 있었다. 앱 루트가 아닌 곳에서 실행하면 로그 파일이 멀쩡히 있는데도 보고서 탭에
  장비가 **0대**로 뜨고(정상: 7대) 점검 로그 목록도 절반만 보였다 — 실제로 재현해서 확인함.
  엑셀/PPTX 산출물 저장 경로까지 CWD 상대라 리포트가 엉뚱한 폴더에 생겼다.
  `AppPaths.terminal_sessions_dir()` / `raw_logs_root()`를 추가해 전부 앱 루트 기준으로 고정.
  (`ARCHITECTURE.md`의 "Known follow-ups" 4번 항목이었음 — 이제 해소.)
- **[치명] 장비목록 저장이 통째로 안 되고 있었다** — `api/inventory_api.py`의 `save_devices()`가
  `di._normalize_device`를 부르는데, 모듈을 셋으로 쪼갤 때 facade(`device_inventory.py`)가
  밑줄 이름은 재노출하지 않아 매번 `AttributeError`로 죽었다. 자동저장·저장 버튼 모두 무효.
  공개 이름 `normalize_device`로 승격하고 재노출(`_normalize_device`는 내부 호환용 별칭으로 유지).
- **환경 설정의 배치 값이 분석·보고서에 전혀 반영되지 않았다** — 저장은 `local_batching` 키에
  하면서(레거시 `batching` 키는 삭제까지 함) `api/analysis_api.py`·`api/report_api.py`는
  `batching`만 읽고 있었다. 정본 키를 읽고 레거시는 폴백하도록 통일.

**장비 목록: 추가·수정하면 자동으로 연결 확인 (`engine/device_probe.py`, `web_ui/js/inventory-probe.js`, 신규)**
- IP·포트·사용자명·비밀번호를 고치면 0.9초 뒤 자동으로 SSH 접속을 시도한다. 버튼을 누를
  필요가 없고, 자동저장이 꺼져 있어도 동작한다 — 저장된 인벤토리가 아니라 **화면에서 편집
  중인 값**을 그대로 서버로 보내기 때문(`probe_device_config`).
- 결과를 행 배경색으로 표시: 성공=초록, 실패=빨강, 확인 중=회색. 라이트/다크 각각 따로
  토큰을 정의했다(`--row-ok-bg` 등, `style.css`) — 다크 기준으로만 잡으면 흰 배경에서
  형광펜처럼 튀어서 글자가 안 읽힌다.
- **접속에 성공하면 장비가 알려준 hostname으로 목록의 이름을 자동 정정한다.**
  `AUTO-101` 같은 임시 이름이 실제 `Core1`으로 바뀐다. 다른 행이 이미 그 이름을 쓰고 있으면
  바꾸지 않고 사유를 표시한다(중복 이름은 인벤토리에서 서로를 덮어씀).
- 기존 `check_reachability()`(소켓 포트만 확인, 대시보드 집계용)는 그대로 두고 그 위에
  얹었다. 결과가 3단계로 갈린다: 포트 안 열림 / 포트는 열렸는데 인증 실패 / 접속 성공.
  "IP는 맞는데 계정이 틀린" 경우를 "장비 다운"과 구분해서 보여주기 위함.
- hostname 조회는 `show hostname`(Arista) → `show running-config | include ^hostname`(Cisco)
  → `hostname`(Linux) 순으로 시도하고, exec 채널을 막아둔 장비는 대화형 쉘 프롬프트에서
  읽어내는 방식으로 폴백한다. 역할(role)이 리눅스면 `hostname`만 쓴다.
- 불러오기(CSV/Excel)와 IP 자동 생성 직후에는 만들어진 장비 전체를 병렬로 한 번에 확인한다.
  수동으로 돌리는 "전체 연결 확인" 버튼도 추가.
- 셀에는 짧은 결과("연결됨 · Core1" / "인증 실패")만 넣고 전체 문구(주소·실패 사유·이름
  변경 내역)는 툴팁으로 돌렸다. 처음엔 긴 문장을 셀에 그대로 넣었더니 표가 컨테이너보다
  175px 넓어져 정작 결과가 화면 밖으로 밀려났다(브라우저 테스트로 잡음). 상태 문구를
  버튼 아래로 내리고 이 표의 셀 여백만 줄여서 1280px 창에서 가로 스크롤 없이 다 보이게 함.

**같이 고친 버그**
- `select[data-field]`(역할 콤보박스)에 change 리스너가 안 걸려 있어서 **역할을 바꿔도
  저장이 안 되고 있었다**(`input[data-field]`만 잡고 있었음). 역할은 hostname 조회 커맨드를
  고르는 데도 쓰이므로 반드시 반영돼야 한다.
- `paramiko.DSSKey`를 하드코딩하고 있어서 **paramiko 4.x 이상이 깔린 PC에서는 SSH 키 접속이
  `AttributeError`로 통째로 죽었다**(DSA 폐기로 클래스가 제거됨). 있는 키 클래스만 골라
  쓰도록 수정 — paramiko 5.0.0으로 실제 재현하고 확인함.
- 연결 확인 결과가 도착해 표를 다시 그릴 때 입력 중이던 칸의 포커스와 커서 위치가 날아갔다.
  다시 그리기 전후로 위치를 기억·복원하게 수정.

**SSH 접속 로직 단일화 (`engine/ssh_client.py`, 신규)**
- 비밀번호 / 개인키 파일 / 붙여넣은 키 본문을 처리하는 로직이 `api/terminal_session_api.py`
  안에만 있었다. 자동 연결 확인도 똑같이 접속해야 해서 engine 계층으로 내리고 양쪽이 공유한다
  — 갈라지면 "터미널은 되는데 연결 확인은 실패" 같은 어긋남이 생긴다.

**진입점 하나로 통합**
- `main.py`가 이제 pywebview + Material Design 3 웹 UI를 띄우는 **유일한 진입점**.
  기존 `gui_web.py`의 내용을 `main.py`로 옮기고 `gui_web.py`는 삭제.
- CLI 채점 진입점 폐기 — `--project` / `--pipeline` 옵션과 `if __name__ == "__main__"`의
  CLI 분기 제거. 모든 조작은 UI에서 한다.

**채점 로직을 engine 계층으로 분리 (`engine/grading.py`, 신규)**
- `main.py`에 있던 `init_project` / `load_lab_config` / `get_all_commands` /
  `real_collect` / `grade_via_pipeline`을 그대로 옮김. 로직 변경 없음.
- 호출부를 `import main as main_module` → `from engine import grading`으로 교체
  (`api/grade_api.py`, `engine/scheduler.py`). 진입점 모듈을 라이브러리처럼
  import하던 구조(`python main.py` 실행 시 main.py가 `__main__`과 `main` 두 번
  로드되는 문제)를 없앰.

**폐기(삭제)**
- 레거시 `grade()` 경로 — Stage 이름을 직접 호출하던 하위호환 채점 경로.
  이제 Pipeline 경로가 유일하다. 딸린 `adapt_raw_to_collected()`도 함께 제거
  (동일 역할은 `pipeline/steps.py`의 `ParserStep`이 이미 담당).
- `web_ui/js/core.js`의 `MOCK` 폴백 — 브리지가 없을 때 가짜 대시보드 수치
  (health 66 / critical 3 등)를 실제 값처럼 렌더하던 코드. 이제 `null`을 반환하고
  콘솔에 경고만 남긴다.
- 한 번 쓰고 남은 마이그레이션 산출물 `PHASE3/4/5_MIGRATION_REPORT.json`,
  `migration_report.json`(실행 중 생성되므로 `.gitignore`로 이동).

**문서 갱신**
- `README.md` 전면 재작성 — 실행법/최초 설정 순서/Pipeline 단계/폴더 구조.
  더 이상 존재하지 않는 `gui.py`·`setup_check.py`·`demo_run.py`·`--mock` 안내 제거.
- `실행방법.txt` 재작성 — `python main.py` 하나로 정리, 자주 나는 오류 2건 추가.
- `ARCHITECTURE.md` / `MIGRATION_GUIDE.md`의 `gui_web.py` 참조를 `main.py`로 교체,
  레이어 다이어그램에 `main.py`와 `engine/grading.py` 추가.
- `api/misc_api.py`의 아키텍처 현황 갱신 — "grade() 완전 폐기"를 pending에서 제거.

## v0.0.15 — 워크스페이스 자동 마이그레이션 + 프로덕션 준비도 리뷰

**레거시 → 신규 워크스페이스 자동 마이그레이션 (`engine/migration_manager.py`, 신규)**
- 앱 시작 시(`main.py`, `webview.create_window()` 이전) 1회 자동 실행. 레거시 데이터
  (`labs/`, `history/`, `config_snapshots/`, `raw_logs/`, `config/`)가 있고 아직 이번
  `migration_version`을 적용하지 않았을 때만 동작 — 이미 마이그레이션됐으면 버전 파일
  (`data/.workspace_version.json`) 확인 한 번으로 즉시 스킵.
- 수정 전 반드시 `migration_backup/<timestamp>/`에 레거시 트리 전체를 복사(원본은 항상
  COPY만 하고 절대 이동/삭제하지 않음). 대상 파일이 이미 있으면 건너뜀(사용자 파일 절대
  덮어쓰지 않음). `migration_report.json`에 migrated/skipped/failed/warnings 전부 기록.
- `rollback_migration()`으로 되돌리기 가능(새로 만든 파일만 삭제, 원본은 애초에 손대지
  않았으므로 안전). `schema_version`/`workspace_version`/`profile_version`/
  `migration_version` 4종 버전 마커 도입, 향후 스키마 변경은 `UPGRADE_FUNCS`에 함수
  추가만으로 확장.
- 격리된 사본으로 실제 동작 검증: 1회차 마이그레이션 정상 수행 → 2회차 자동 스킵(멱등성
  확인) → 롤백 정상 복구까지 3단계 전부 확인.

**프로덕션 준비도 리뷰 (아키텍처/스레드안전/파일시스템안전/에러처리/타입힌트/설정관리/
자원정리/성능/GUI연동/하위호환/마이그레이션 안전성 전수 검토)**
- `ARCHITECTURE.md`, `WORKSPACE_STRUCTURE.md`, `DEVELOPER_GUIDE.md`,
  `MIGRATION_GUIDE.md` 신규 작성 — 레이어링, 5대 매니저(ProfileManager/StorageService/
  RunManager/LogManager/ReportManager) 책임 경계, 폴더 구조, 개발 규칙, 마이그레이션
  안전성을 문서화.
- `api/misc_api.py`의 `HistoryApiMixin`이 `history/{project_id}`를 CWD 상대경로로 직접
  읽고 쓰던 버그 수정 — `engine/history.py`에 `list_sessions`/`load_session`/
  `delete_session` 신규 추가, `AppPaths.history_root()` 경유로 통일(실행 위치가 앱
  루트가 아니면 조용히 실패하던 문제 제거).
- `engine/profile_manager.py._write_json`을 원자적 쓰기(임시파일 + `os.replace`)로 변경
  — 기존에는 쓰는 도중 프로세스가 죽으면 `profile.json` 등이 잘린 채 남을 수 있었음.
  `core/storage_service.py`가 이미 쓰던 방식과 통일.
- 리뷰에서 발견했지만 이번 패스에서 일부러 미루고 문서에만 남긴 항목(더 큰 리팩터링이
  필요해 단독 검토 필요): `StorageService.create_run()`과 `RunManager.create_run()`의
  중복 구현, `api/customer_profile_api.py`가 5대 매니저를 우회해 메타 YAML을 직접
  읽고 쓰는 문제, 사용처 없는 매니저 메서드 13개, 마이그레이션 이후에도 레거시 경로를
  직접 읽는 3개 API 모듈 — 전부 `ARCHITECTURE.md`의 "Known follow-ups"에 정리.

## v0.0.14 — 모듈화 + 미구현 UI 3개 + Gemini Provider + IP 연결테스트

**모듈화(최적화)**
- `gui_web.py`(428줄, 관심사 10개가 한 클래스) → `api/` 패키지 12개 mixin으로 분리
  (base/project/dashboard/discovery/grade/report/misc/catalog/inventory/connection/analysis/inspection/knowledge_api.py).
  `gui_web.py`는 이제 mixin 합성만 하는 30줄짜리 진입점. 36개 메서드 전부 충돌 없이 합성 확인, 회귀 테스트 통과.
- `web_ui/app.js`(689줄 단일파일) → `web_ui/js/` 14개 페이지별 파일로 분리. 전체 문법 통과, 렌더링 검증(색상 히스토그램 대조).
- 과정에서 실제 버그 1건 발견·수정: `plugins/vendors/base.py`의 `list_vendors()`가 `arista.py`를
  명시적으로 import 안 하면 빈 리스트를 반환하던 문제 — `plugins/vendors/__init__.py`에서
  자동 import되게 수정.

**미구현이던 UI 탭 3개 실제 구현**
- **Analysis** — Rule Engine 판정/Evidence 샘플/AI 요약/Health Score/등록된 Vendor·Parser 현황을
  한 화면에 통합. 실제 세션 데이터로 검증(VLAN 18/18, STP evidence 5건 등 정상 표시 확인)
- **Inspection Profile** — Stage 의존관계(depends_on)·실행 커맨드·체크 개수를 구조화된 표로 표시
  (읽기 전용, 편집은 다음 버전)
- **Knowledge** — 프로젝트별 Markdown 문서 탐색기, 추가/조회/저장/삭제 전체 CRUD 실제 검증

**Gemini API Provider 추가**
- PDF 번역 프로그램에서 이미 검증된 "Gemini API + 로컬 NPU 폴백" 패턴을 AI 분석 라우터에
  동일하게 반영. Anthropic → Gemini → 로컬 → 규칙기반 순서로 폴백, Cloud 승인 게이트 동일 적용.
  체인 테스트로 정상 폴백 확인.

**Device Inventory 신규 기능**
- 장비별 **"연결 테스트" 버튼** 추가 — `check_device_reachability()` API 신규, 수정 중인 IP를
  즉시 저장 후 소켓 레벨로 도달가능성 확인, 결과를 초록/빨강 배지로 표시. 버튼이 호출하는
  API 시퀀스를 직접 재현해 검증(저장→테스트→"연결 실패" 정상 반환 확인).

**Architecture 탭 갱신**: 반영됨 13개→22개, 반영예정 항목도 최신화(Inspection Profile 편집
기능을 새 pending 항목으로 추가).

## v0.0.13 — 손상된 gui_web.py 복구 + _project()/_paths() 헬퍼 정식 적용

사용자가 다른 AI로 리팩토링한 gui_web.py를 검토한 결과, 실제로는 심각하게 손상되어
있었음("Syntax Error 없음"이라는 주장과 달리):

**발견된 문제**
1. 파일 최상단에 `get_architecture_status()`의 pending 리스트가 모듈 docstring과
   뒤섞여 클래스/함수 밖에서 실행되는 고아 코드로 남아있었음
2. `get_dashboard()`에 실제 IndentationError(108~110번 줄)
3. **v0.0.12에서 추가한 Health Score 로직이 통째로 사라짐**(v0.0.11 기준으로
   되돌아간 것으로 추정) — PASS/TOTAL 비율 계산으로 회귀
4. `get_architecture_status()`가 절반 이상 잘려서 "pending" 키 자체가 사라짐 —
   `app.js`에서 `status.pending.length` 호출 시 즉시 에러 나는 상태
5. 미완성 `safe_api` 데코레이터가 정의만 되고 어디에도 적용 안 됨(적용했다면
   반환값 포맷이 바뀌어 `app.js`와 계약이 깨졌을 것)

**조치**: v0.0.12 정상본을 기준으로 복구하고, 제안된 리팩토링 아이디어(`_project()`/
`_paths()` 헬퍼)만 정확히·안전하게 적용:
- `_project()`는 프로젝트 없으면 예외를 던지므로, 기존에 "조용히 빈 값 반환"하던
  메서드 16개는 `try/except RuntimeError`로 감싸 **기존 반환값을 정확히 그대로 보존**
  (기능 변경 금지 원칙 — 헬퍼 도입이 동작을 바꾸면 안 됨)
- `pm.get_active_project()` + `pm.project_paths()` 반복 호출 패턴 전체 제거
- `safe_api`(미완성·미사용 데코레이터)는 제거 — 프론트엔드 계약과 안 맞는 상태로
  방치하는 것보다 깨끗이 제거하는 게 안전

**회귀 검증**: Health Score(79점) 복구 확인, 카탈로그 27개/장비 7대/Architecture
13+7개 항목 전부 정상, 프로젝트 미선택 시 각 메서드가 예외 대신 기존과 동일한
빈 값을 반환하는 것까지 확인.

## v0.0.12 — Health Score + Finding 5단계 severity/status

다른 인턴이 제안한 "Enterprise Network Inspection Platform" 설계안 검토 후 핵심 2가지 반영.

- **Finding severity 5단계로 확장**: Critical/High/Medium/Low/Info (기존 3단계에서 세분화)
- **Finding status 5단계로 확장**: Open/Investigating/Fixed/Ignored/Closed (Jira 스타일),
  `mark_status()`로 Fixed/Closed 전환 시 resolved_at 자동 기록. result/severity는 불변 유지.
- Finding에 `interface`/`memo`/`resolved_at` 필드 추가(문서 스키마 반영)
- **`core/health_score.py` 신규** — 100점에서 시작해 Rule 위반마다 감점(Critical -30/High -15/
  Medium -5 등, check_id별 override 가능: power_status -30, cpu_usage -5 등 문서 예시 그대로).
  Device→Project 집계 검증(Core1=55, Agg1=95, 프로젝트 평균 83.3 등 계산 정확성 확인)
- Finding 객체와 History JSON(dict)에서 로드한 것 둘 다 지원하도록 설계
- Dashboard에 실제 Health Score + 장비별 점수 카드 연결 — 실행 결과로 직접 검증(79점,
  장비별 55~100점 분포 확인)

**버그 수정**: severity 값 변경(CRITICAL→Critical 등)에 따라 `app.js`의 배지 색상 매핑이
옛 값을 참조하던 것 발견·수정. `device_scores`에 STP root 교차검증용 가짜 "device"인
"(network-wide)"가 실제 장비처럼 섞여 나오던 것 발견·제외.

**아직 반영 안 함(다음 우선순위)**: Inspection Session을 정식 객체로(세션 폴더 구조),
Inspection Wizard(단계별 진행 UI), Device Inventory SQLite 전환, Rack 계층, Collection UI
Device Card 방식.

## v0.0.11 — Report Plugin + Findings/Architecture UI

- `report/base_reporter.py`, `report/reporters.py` — BaseReporter 인터페이스, Markdown/Docx
  등록. 실제 Word 파일(36KB, 유효한 docx 포맷) 생성 검증.
- `engine/history.py` — save_history가 Finding 리스트도 함께 저장(하위호환 유지).
- **웹 UI에 실제 채점 실행 페이지가 아예 없었던 것 발견** — Collection 탭을 Pipeline
  실행 화면으로 신규 구현(이전엔 "준비중" 스텁뿐이었음).
- Findings 탭 실제 구현(Jira 스타일: Severity/Status/Owner/Device/Category/
  Recommendation) — 실행→저장→화면표시 전체 플로우 실제 검증(32건 Finding 확인).
- **Architecture 탭 신규** — 반영된 것 13개/반영 예정 7개를 실제 화면에 체크리스트로 노출.
- Reports 탭에 포맷 선택(Markdown/Docx) 추가, Report Plugin과 연결.

## v0.0.10 — AI Sanitizer/AIContextBuilder + Cloud 승인 게이트

- `core/sanitizer.py` — Cloud AI 전송 직전에만 IP/MAC/호스트명 마스킹, 원본 불변 검증
- `core/ai_context_builder.py` — Finding에서 FAIL/UNKNOWN만 추려 압축 컨텍스트 생성(Sanitizer와 역할 분리)
- `ai_analysis/router.py` — Cloud API는 `user_approved_cloud=True` 없이는 절대 호출 안 되게
  승인 게이트 추가. 3가지 시나리오(미승인/승인+키없음/Finding마스킹경로) 전부 테스트 통과.

## v0.0.9 — 아키텍처 우선순위 리팩토링 (Pipeline/Finding/VendorDriver/Registry)

다른 AI의 아키텍처 리뷰 + 자체 검토 결과, 합의된 우선순위 6가지를 실제 코드로 구현.
전부 "기존 코드를 감싸는(wrap) 방식"으로 진행 — comparator/parsers 내부 로직은 안 건드림.

1. **core/finding.py** — Finding 표준 스키마(severity/status/owner/source 등).
   `with_recommendation()`이 result/source를 건드릴 수 없게 설계 — "AI는 PASS/FAIL을
   못 바꾼다" 원칙을 코드 레벨로 강제. 실제 테스트로 확인(AI가 recommendation을
   붙여도 result="FAIL", source="rule" 불변).
2. **core/context.py** — ProjectContext/SessionContext, 상태만 보관(로직 없음 원칙).
3. **pipeline/step.py, pipeline/pipeline.py** — PipelineStep 인터페이스 + 실행기.
   Stage 추가가 main.py 수정이 아니라 Step 리스트 추가로 끝나게 함(OCP 확보).
4. **plugins/vendors/base.py, arista.py** — VendorDriver. check_id(예: "vlan_status")를
   실제 CLI("show vlan brief")로 변환하는 책임을 이 한 곳에 격리. Cisco/Juniper 추가 시
   이 파일 형태로만 하나씩 늘리면 됨 — Collector/Parser/Comparator 무변경.
5. **plugins/parsers/registry.py** — (vendor, check_id) 키로 파서 자동 탐색. 기존
   parsers/*.py 함수 16개를 그대로 등록만 함(로직 재사용).
6. **rule_engine/rules.py** — comparator.py를 감싸 Verdict를 Finding으로 변환하는
   어댑터. 역변환(Finding→Verdict)도 제공해서 scorer.py는 안 건드림.
7. **engine/command_catalog.py** — 전 항목에 `check_id` 필드 추가(literal command는
   하위호환용으로 유지). `get_enabled_commands_via_driver()` 신규 — VendorDriver 경유
   결과가 기존 literal 방식과 **완전히 일치**함을 실측 검증(27개 항목, 11개 활성 동일).
8. **main.py의 `grade_via_pipeline()`** — 기존 `grade()`는 그대로 두고 Pipeline 기반
   신규 경로 추가(`--pipeline` 옵션). **기존 방식과 신규 방식이 동일 입력에서 완전히
   동일한 채점 결과(VLAN 18/18, STP 3/14, 동일 FAIL 목록)를 냄을 회귀 테스트로 확인.**
   Finding 32개 생성, FAIL 11개, severity/recommendation 정상 부착까지 검증.

**버그 추가 발견·수정**: `rule_engine/engine.py`라는 파일명이 기존 `engine/` 패키지와
이름이 겹쳐서 직접 실행 시 자기 자신을 `engine` 패키지로 잘못 인식하는 문제 —
`rule_engine/rules.py`로 개명해서 해결.

**의도적으로 아직 안 한 것(다음 우선순위)**: 기존 `grade()`는 여전히 존재(하위호환),
`--pipeline` 옵션을 켜야 신규 경로 탐. Cisco/Juniper VendorDriver, AIContextBuilder/
Sanitizer, StorageService 정식화, Report Plugin 구조는 이후 Phase.

## v0.0.8 — Device Inventory 아키텍처 전면 개편

**문제 인식**: IP가 lab_meta.yaml/ip_allocation.yaml에 쪼개져 있고 Collection이 그
파일들을 직접 읽는 구조였음.

- `engine/device_inventory.py` 신설 — 장비 정보(IP·계정·역할·벤더·모델·존·사이트·
  태그·메모·Enable)를 관리하는 유일한 출처로 통합. 기존 프로젝트 자동 마이그레이션.
- IP Pool 자동 할당(Prefix/Start/End→Generate), CSV/YAML/JSON Import, 도달가능성
  체크(check_reachability) 전부 실동작 검증.
- `collector.py` 전면 재작성 — IP를 직접 안 읽고 Inventory에서만 resolve. 가짜 접속
  으로 "Core1→172.30.1.101:22" 정확히 검증.
- 웹 UI 신규 페이지: Device Inventory, Connection(SSH 옵션 전용), Command Catalog.
  Discovery에 "Inventory 등록" 버튼 추가. Dashboard KPI를 Inventory 기반으로 개편.

## v0.0.7 — Material Design 3 웹 UI(gui_web.py) 신규

- Tkinter로는 리플/elevation/모션을 구현할 수 없어 `pywebview` + HTML/CSS/JS로 UI
  레이어 신설(백엔드 로직은 전혀 안 바꿈 — 순수 프레젠테이션 계층 교체).
- 요청하신 색상 팔레트(#111827/#0F172A/#1E293B/#3B82F6 등), 8px 그리드, Radius16,
  TopBar+Sidebar+Content+StatusBar 레이아웃 전부 반영.
- Xvfb+QtWebEngine으로 실제 렌더링 스크린샷 확보, 색상 히스토그램으로 팔레트 일치
  검증(#334155 hover색 등 정확히 매칭).
- 기존 `gui.py`(Tkinter)는 그대로 유지 — 두 UI 병행 가능.


## v0.0.6 — 대규모 기능 보강

체크리스트 대조 결과 확인된 공백을 실제 코드로 메움. 각 기능은 전부 개별 테스트로 실동작 확인함.

**연결/수집**
- Command Catalog를 실제 수집 로직에 연결(기존엔 UI 체크박스만 있고 실제 수집 커맨드 결정에 관여 안 했음)
- `connection.yaml` 기반 Retry 로직 실제 구현(기존엔 설정값만 있고 코드가 안 읽었음) — 재시도 시뮬레이션으로 검증
- Config Snapshot 저장(`config_snapshots/{lab}/{device}_{date}.txt`) 구현·검증

**Parser 8종 신규**
- `show_environment.py`(전원/팬/온도), `show_processes.py`(CPU/메모리), `show_interfaces_status.py`(링크상태/CRC/discard),
  `show_port_channel.py`(LACP), `show_inventory_mlag_vrrp.py`(Inventory/MLAG/VRRP/VARP), `show_routing_neighbor.py`(OSPF/BGP/EVPN/VXLAN),
  `config_diff.py`(설정 변경이력 대조)
- 테스트 중 실제 버그 4건 발견·수정: 에러카운터 Tx컬럼 누락, BGP 필드개수 오류, VRRP 헤더 오인식 등 (기존 4건에 이어 누적 8건)

**Discovery**
- `lldp_discovery.py` 신규 — 살아있는 장비의 `show lldp neighbors` 기반 실토폴로지 재구성 + 설계도(.unl) 대조(matched/missing/unexpected)

**History**
- `compare_sessions()`, `compare_check_level()` 추가 — Stage별/개별체크 단위 PASS·FAIL 증감 추적, 실동작 검증

**AI 분석**
- `ai_analysis/rule_based.py` — API 없이 항상 동작하는 규칙기반 요약·이상탐지·우선순위·조치권고
- `ai_analysis/router.py` — API → 로컬NPU → 규칙기반 순서 폴백 체인, 전부 실패해도 규칙기반으로 항상 결과 반환 검증

**보고서**
- `report/markdown_report.py` — Executive Summary/단계별결과/특이사항/조치권고 포함 Markdown 자동생성, `main.py`에 통합
- docx(Word) 생성 함수도 추가(python-docx 설치 시에만 동작, 없으면 안내 후 정상 스킵)

**GUI 신규 탭 5개**
- 수집(카탈로그+채점 커맨드 미리보기, 단독 수집 실행), 이력(세션 목록+diff 비교창), 보고서(생성+미리보기), 연결 설정(pre-flight/retry 값 편집·저장), AI 설정(제공자 체인 현황 표시)
- 전체 9개 탭 전환 + 저장/실행 플로우 Xvfb로 재검증

**main.py 통합**
- `grade()`에 AI분석·보고서생성·history diff 출력 전부 통합, 회귀테스트 통과

**아직 미구현(정직하게 남김)**
- 정기점검 고객사 프로파일(다른 프로젝트에서 설계만 진행, 이 코드베이스엔 미이식)
- AI 설정 UI에서 실제 API 키/provider 편집(현재는 현황 표시만, 저장은 미지원)
- OSPF/BGP/EVPN 파서는 표준 포맷 기준 범용 파서 — 벤더별 변형 출력까지 커버 안 함
- LLDP discovery는 파서·대조 로직만 완성, 실제 실행(main.py 연동)은 다음 버전

## v0.0.5
- `gui.py` 머티리얼 디자인 스타일 리팩토링 — ttk Style 커스텀 색상(accent/success/danger), 카드형 컨테이너, 사이드바 활성메뉴 강조, 대시보드 메트릭카드+색상 프로그레스바, 카탈로그 카테고리 색상뱃지
- Xvfb로 실제 창 띄워 화면 캡처까지 검증(스크린샷 별도 제공)

## v0.0.4
- **신규**: `gui.py` — Tkinter 기반 실제 동작하는 데스크톱 UI 추가(표준 라이브러리만 사용, 추가 설치 불필요)
  - 상단: 프로젝트 선택/생성/이름변경/삭제
  - 좌측 네비: 대시보드 / Discovery / 커맨드 카탈로그 / 채점 실행
  - 전부 실제 백엔드 함수(project_manager, command_catalog, unl_parser, main.grade)에 연결해서 동작 확인함(Xvfb 가상 디스플레이로 실제 창 띄워 테스트)
  - 확인된 동작: 프로젝트 목록·전환, `.unl` 분석 결과 표시, 카탈로그 체크박스 토글→저장→재로딩 반영, 커스텀 커맨드 추가/삭제, 채점(mock) 실행 결과 표시
- 테스트 과정에서 생긴 임시 이력/로그 파일 정리, 카탈로그 기본값(`show version` 활성화) 복구

## v0.0.3
- **[긴급 수정] Windows `UnicodeDecodeError` 수정** — `open()` 호출 11곳에 `encoding="utf-8"` 명시 추가. 원인: Windows 기본 인코딩(cp949)이 UTF-8 yaml 파일을 못 읽던 문제.
- `setup_check.py`에 인코딩 자가진단 항목 추가

## v0.0.2
- `setup_check.py`, `실행방법.txt`, `requirements.txt` 추가 (Windows 실행 문제 대응)
- `main.py` ↔ `project_manager` 연결 (활성 프로젝트 자동/명시 전환, `--project` 옵션)
- 커맨드 카탈로그(`engine/command_catalog.py`) 추가 — 필수/선택/커스텀 활성화 관리, 프로젝트별 독립 저장
- 프로젝트 다중 관리(`engine/project_manager.py`) 추가 — 생성/이름변경/삭제, 한글 이름 슬러그 충돌 버그 수정
- 병렬 연결 수 제한 제거 + `engine/benchmark.py`(성능 테스트·추천값 자동 산출) 추가
- VPN/내부망 사전점검(`pre_flight_check`) 추가

## v0.0.1
- 최초 버전: `.unl` Discovery, VLAN/STP 파서, comparator/scorer, collector(netmiko), history 저장까지 파이프라인 최초 완성 및 실행 검증
- 발견 및 수정한 버그 4건: 커맨드 목록 다중 정의, STP 파싱실패 은폐, 좌표 완전일치 취약점, IP 미입력 시 크래시

