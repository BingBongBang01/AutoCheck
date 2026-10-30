# AutoCheck 개선 계획 (V0.5.9 → V1.0)

> 작성 2026-10-27 · 대상 `V0.5.9/` 전체(Python 약 2.6만 줄 — 빈 줄 제외·테스트 포함, 웹 UI JS 30개 파일)
> 방법: 4개 영역(실시간 감시·로그분석 / 점검·수집·AI / 워크스페이스·보고서·API / 웹 UI·빌드·테스트·문서) 정적 리뷰 + Windows 실환경 테스트 실행
> 표기: **✔** = 이 문서를 쓰며 코드를 직접 다시 열어 확인 · **(의심)** = 정황뿐, 착수 전 재현 필요 · 표시 없음 = 영역 리뷰에서 코드 읽기로 확인(실행 재현은 안 함)
> 공수(1인 추정): **S** ≤1일 · **M** 2~5일 · **L** 1~2주 이상

> **진행 현황 (2026-10-27)**: **0~3단계 완료** ✅ — git 저장소 시작(원본은 태그 `v0.5.9`). 테스트는 450 passed/2 failed/29 skipped에서 **931 passed/0 failed/0 skipped**가 됐다(node 설치 후 JS 테스트 포함; 3단계에서 퇴역 코드의 테스트를 지우고 구조 테스트를 더했다). 각 항목의 **결과** 줄 참고. 1단계에서 미룬 것: CSP(1-3), 모델·API 버전 이전(→4-2). 2단계에서 미룬 것: data 폴더의 불변 id 매핑(2-1), 보관함 화면(→4-10), 실기 로그 코퍼스 검증(2-11), 나눔고딕 번들(2-15), API 반환 형태 통일·공용 확인 모달(2-17). 3단계에서 미룬 것: 무인 정기점검 재작성(→4-3), 분석 작업 취소 연결·작업 센터(→4-9), 프롬프트 정의 통합(2-10에서 넘어옴 → 4-5 — 세 정의의 차이(첫 글자 숫자·`#` 앞 공백·길이)가 판정 동작이라 실기 코퍼스 없이 고를 수 없다), 전역 변수 정리·나머지 모달 3곳(3-7), 표시용 시각 포맷 48곳(3-8, 근거 없이 통일하지 않음). 실장비·실제 EXE·실제 화면으로는 확인하지 않았다(가짜 netmiko·paramiko, spec 모의 실행, node 로 JS 함수 실행으로 검증). 4단계: 4-1 자격증명 금고·4-2 클라우드 AI 전송 미리보기·승인·4-3 무인 정기점검·4-4 알림 채널·4-5 멀티벤더(Cisco)·4-6 SSH 호스트키 관리·4-7 회차 비교·4-8 실시간 감시 확장·4-9 작업 센터·4-10 운영 편의 완료로 4단계를 마쳤다(1300 passed — Cisco 는 실장비 미검증, 실출력 골든 픽스처는 아직 없다). 5단계: 5-1 문서 정정·재배치(문서는 `docs/`로 옮겼다)·5-2 릴리스 점검 스크립트 완료, 5-3 v1.0 완료 정의 대조 완료(1315 passed) — v1.0 을 선언하려면 실장비가 있어야 하는 확인 둘(실시간 감시 육안 확인, 실출력 골든 픽스처)과 릴리스 절차가 남았다. 릴리스 준비: VERSION·CHANGELOG 제목·README 머리를 0.6.0 으로 올렸다(태그 `v0.6.0`과 벤치마크 기준선은 사람이 정한다).

## 요약

**진단** — 기능 폭(실시간 감시·구성도·정기점검 보고서)과 최근 버전의 정확성 수정 수준은 높다. 반면 세 가지가 약하다.

1. **보안·데이터 보호가 '개인 PC 도구' 수준이다.**
   - 원본 로그가 마스킹 없이 클라우드 AI로 나간다.
   - 자격증명이 평문으로 저장된다.
   - 웹 UI의 이스케이프가 불완전하다.
2. **README가 핵심으로 내세우는 채점 파이프라인은 실제로 끊겨 있다.** UI 진입점이 없고, 수집 결과가 파서에 전달되지 않는다.
3. **작업 기반이 없다.** 버전 관리가 없고, Windows에서 테스트가 녹색이 아니다. 그래서 수정할수록 위험이 쌓인다.

### 가장 먼저 할 7가지

| # | 할 일 | 왜 급한가 | 공수 | 항목 |
|---|---|---|---|---|
| 1 | ✅ git 도입 + Windows 테스트 녹색화 | 이후 모든 수정을 되돌리고 검증할 수 있어야 한다 | S | 0-1, 0-3 |
| 2 | ✅ 기본 명령 카탈로그에서 `conf` 제거 | 세션 터미널 점검이 끝나면 작업자 터미널이 설정 모드에 남는다. 다음 입력이 곧 설정 변경이 된다 ✔ | S | 1-1 |
| 3 | ✅ 클라우드 AI 전송 시 마스킹 강제, API 키의 로그 유출 차단 | 고객 설정·키·IP가 외부로 나간다. Gemini 키가 오류 로그에 평문으로 남는다 ✔ | S~M | 1-2 |
| 4 | ✅ 웹 UI 이스케이프 통일 | 실시간 감시 '해결/무시' 버튼이 지금 동작하지 않는다 ✔. 장비명을 통한 XSS가 가능하고, 브리지가 평문 비밀번호 조회와 SSH 일괄 입력을 노출한다 | M | 1-3 |
| 5 | ✅ 워크스페이스 내보내기 수정 | 내보낼 때마다 이전 zip을 포함해 2배로 커진다. 평문 계정 파일도 들어간다 ✔ | S | 1-5 |
| 6 | ✅ 프로파일 생성·이름변경을 트랜잭션으로, 한글 고객사 경로 충돌 수정 | 회차 사이, 고객사 사이에 데이터가 섞인다 ✔ | S~M | 2-1, 2-2 |
| 7 | ✅ 실시간 감시의 상태 저장·체크리스트 판정 수정 | 해결된 장애가 재시작 후 미해결로 돌아온다. fail이 warn으로 강등된다 ✔ | S | 2-8, 2-9 |

### 결정이 필요한 것

| # | 결정 | 권장 | 근거 |
|---|---|---|---|
| D1 | 채점(grading) 파이프라인을 **퇴역**할지 **재건**할지 | **퇴역.** 무인 점검은 UI가 이미 쓰는 '수집 → 로그분석 → 정기점검 보고서' 경로로 다시 잇는다 → ✅ 권장안으로 결정·적용(3-1). 무인 점검 재작성은 4-3 | 아래 '근거 상세' 참조 |
| D2 | 지원 Python 버전 | **3.12 이상으로 문서를 정정한다** → ✅ 권장안대로 적용(0-5). 낮은 버전이면 시작할 때 안내하고 멈춘다 | 아래 '근거 상세' 참조 |
| D3 | 원본 로그의 클라우드 AI 전송을 계속 허용할지 | 기본값은 로컬 AI·규칙기반. 클라우드는 강제 마스킹과 미리보기 승인을 거친 뒤에만 쓴다 | 마스킹을 하더라도 고객사의 반출 정책을 확인해야 한다 |
| D4 | Cisco 지원 범위와 시점 | IOS/IOS-XE를 먼저, NX-OS는 다음 | 현재는 수집 단계부터 `arista_eos`로 고정돼 있다 ✔. 실기 출력 샘플 확보가 전제다(부록 A) |
| D5 | 숨겨진 UI 기능(프로파일 zip 가져오기·내보내기·이름변경·삭제 모달) | 보안 수정(1-4) 후 워크스페이스 탭으로 복원 → 1-4 완료로 전제 충족 | `openProfileModal()`을 호출하는 곳이 없다 ✔ |

근거 상세:

- **D1 (채점 파이프라인)**
  - 사이드바에 채점 화면이 없다(`web_ui/js/core-navigation.js:50` 주석) ✔.
  - 수집 출력이 `bulk_execution` 키 하나에 담긴다(`engine/collector.py:140`) ✔. 파서는 명령 문자열로 매칭하는데(`pipeline/steps.py:87-90`) ✔, 이 키는 한 번도 걸리지 않는다. 즉 점수와 이력이 장비 상태와 무관하다.
  - 재건 비용은 L(3~4주)이다. 고칠 목록은 부록 B에 있다.
- **D2 (Python 버전)**
  - 실제 최소 버전은 3.12다. `engine/topology_svg.py:203`이 f-string 안에서 같은 따옴표를 재사용한다(PEP 701) ✔.
  - EXE로 배포하므로 사용자 PC의 Python과는 무관하다. 개발 PC는 3.14다.

### 단계 개요

| 단계 | 목표 | 버전 | 추정(1인) |
|---|---|---|---|
| 0 작업 기반 ✅ | 버전 관리·테스트·개발 환경부터 갖춘다 | v0.6.0 | 3~4일 |
| 1 보안·안전 긴급 수정 ✅ | 외부 유출을 막고 운영 장비를 안전하게 한다 | v0.6.x | 1.5~2주 |
| 2 데이터 무결성·판정 정확성 ✅ | 데이터 섞임·유실과 실시간 오판을 없앤다 | v0.7.x | 3~4주 |
| 3 구조 정리 ✅ | D1을 반영하고 죽은 코드·중복을 정리한다 | v0.8.x | 3~4주 |
| 4 기능 추가 | 자격증명 금고·무인 점검·알림·멀티벤더 등 | v0.9.x | 6~8주 |
| 5 문서·릴리스 | v1.0 완료 정의를 충족한다 | v1.0.0 | 1주 |

- 추정에 실장비 검증 시간은 포함하지 않았다.
- 1·2단계 안의 항목은 서로 독립적이라 순서를 바꾸거나 병행할 수 있다.
- 각 항목은 독립 커밋으로 되돌릴 수 있어야 한다(OPTIMIZATION_PLAN과 같은 원칙).

---

## 현재 상태 진단

### 실제 기능과 README의 차이

사이드바 메뉴는 14개다(`web_ui/index.html:34-53` ✔):
워크스페이스, 장비 목록, 명령어 카탈로그, 세션 터미널, 실시간 감시, 점검 로그, 원본로그분석, 마스킹, 네트워크 구성도, 대시보드, 보고서, 지식베이스, 환경 설정, 전체 로그.

README의 작업 흐름은 이와 다르다. README가 안내하는 '수집/채점', 'EVE 구성 불러오기', '점검 항목', '발견사항·분석·이력' 메뉴는 화면에 없다.

실제 주 흐름은 다음과 같다.

1. 세션 터미널에서 점검하면 `runs/<run>/raw`에 원본 로그가 쌓인다.
2. 로그 규칙 엔진이 판정한다.
3. 정기점검 보고서(Excel/PDF)를 만든다.

실시간 감시와 구성도가 이 흐름 옆에서 지원한다.

### 테스트 실측

Windows 11, Python 3.14.7에서 실행했다. 실제 사용자 데이터(`Documents\AutoCheck`)를 건드리지 않도록 포터블 모드 사본에서 돌렸다.

```
python -m pytest tests -q   →   450 passed, 2 failed, 29 skipped (5.8초)   ✔
```

- **실패 2건** — `tests/test_log_cache.py`의 `test_text_is_cached`와 `test_rewritten_file_is_reread`다.
  - Windows의 `Path.write_text`는 CRLF로 쓰는데, `core/text_io.read_log_text`는 줄바꿈을 정규화하지 않는다.
  - 테스트가 Linux에서만 검증됐다는 신호다.
- **skip 29건** — node가 설치되지 않아서다. 그래서 JS 동작 테스트가 개발 PC에서 한 번도 돌지 않는다(`test_adaptive_poller.py` 8건, `test_realtime_delta_client.py` 21건).
- `compileall` 경고는 0건이다.

### 테스트 커버리지 지도

| 모듈 그룹 | 테스트 |
|---|---|
| 실시간 감시 엔진(realtime_monitor·baseline_diff·state_poller·rule_stream) | 두터움 |
| 로그 분석(log_rule_engine·log_analysis·log_cache), 구성도, LLDP 파서 | 있음 |
| profile/run 관리·log_storage·baseline_store·보고서 판정 | 일부 |
| **log_masking**(외부 공유용 마스킹), migration, report_manager, 보고서 렌더러(PDF/PPTX/MD/HTML) | **없음** |
| grading, collector, scheduler, pipeline, rule_engine, comparators, alarm, 파서 12개 중 11개 | **없음** |
| ssh_client, device_probe, device_inventory, command_catalog, cloud_providers, app_logger | **없음** |
| api 믹스인(약 28개) | 4개만 |

### 기존 후속 과제(ARCHITECTURE.md Known follow-ups) 현황

| # | 상태 | 비고 |
|---|---|---|
| 1 run 생성 경로 이중화 | 부분 해결 | 아래 참조 |
| 2 customer_profile_api의 raw open() 쓰기 | 부분 해결 | 아래 참조 |
| 3 호출자 없는 매니저 메서드 | 미해결 | 연결된 것은 `abort_run` 하나다. LogManager 공개 메서드 17개 중 쓰이는 것은 2개다 |
| 4·5 레거시 경로 | 해결 | — |
| 6 전체 트리 스캔 | 해결(단서 있음) | `core/context_cache.py`로 캐시한다. 캐시가 빗나가면 여전히 전체를 스캔하고 다시 기록한다 |
| 7 시각 포맷 | 부분 | 헬퍼(`core/utils/datetime.py`)를 쓰는 곳은 1곳이다. 실시간 경고의 ts에는 날짜가 없다(2-8) |

- **#1**: 실제 생성은 모두 `RunManager.create_run`을 거친다. 남은 문제는 두 가지다.
  - 공개 진입점이 3개다. 그중 `ProfileManager.create_run`은 호출처가 0이다.
  - `StorageService.create_run`이 다른 스키마로 한 번 더 기록한다.
- **#2**: 쓰기 5곳은 원자적 쓰기로 바뀌었다. 다만 캐시가 빗나갈 때마다 메타 전체를 다시 쓴다(2-7).

---

## 0단계 — 작업 기반 (v0.6.0)

모든 다음 단계의 선행 조건이다.

### 0-1. 버전 관리 도입 · S ✅

**결과**
- Git for Windows를 설치하고 이 폴더를 저장소로 만들었다. 수정 전 원본은 태그 `v0.5.9`이고, 이후 변경은 항목별 커밋이다.
- `.gitignore`를 보강하고, `.gitattributes`로 줄바꿈 변환을 껐다. 원본 파일이 전부 LF다.
- 다른 PC의 기존 저장소는 확인하지 못했다. 있다면 이 저장소의 커밋을 그쪽으로 옮기면 된다.

**현재**
- 이 폴더는 git 저장소가 아니다 ✔. 버전마다 폴더를 복사한다(V0.5.0 … V0.5.9). `.gitignore`는 있지만 쓰이지 않는다.
- 다른 PC에 저장소가 있을 가능성이 있다. 근거는 두 가지다.
  - `imports_dump.txt`의 경로(`C:\Users\USER\Documents\github\AutoCheck\V0.5.7\…`) ✔
  - OPTIMIZATION_PLAN의 'git 이력 확인' 언급

**변경**
- 기존 저장소가 있으면 그것을 정본으로 삼아 V0.5.9를 반영한다.
- 없으면 `git init`을 하고, 초기 커밋에 태그 `v0.5.9`를 단다.
- 이후에는 폴더 복사를 그만두고 태그로 버전을 관리한다.
- `.gitignore`에 추가할 것: `build/`, `dist/`, `autocheck_data/`(포터블 모드의 데이터·자격증명), `migration_backup/`, `_workspace_global/`

### 0-2. 소스 트리 정리 · S ✅

**결과**
- 번들 config만으로는 마이그레이션하지 않는다. 백업·보고서·전역 설정 보관본은 사용자 데이터 폴더에 쓴다.
- 산출물 3벌과 `imports_dump.txt`를 지웠다.
- 회귀 테스트 `tests/test_migration_paths.py` 4건을 추가했다. 수정 전 코드에서는 2건이 실패한다.

**현재**
- `migration_backup/`(3벌) ✔와 `_workspace_global/` ✔가 소스 트리에 있다.
  - 개발 실행 때 마이그레이션이 번들 `config/`를 '레거시'로 판정하고, 산출물을 app_root에 쓰기 때문이다(`engine/migration_manager.py:91-93, 150-165, 258`).
- `imports_dump.txt`는 V0.5.7 시절의 덤프로, 아무 곳에서도 참조하지 않는다.

**변경**
- 판정에서 번들 config를 빼고, 산출물은 user_data_root에 쓰게 고친다.
- 그런 다음 위 세 항목을 삭제한다.

**검증**: 개발 모드로 `python main.py`를 실행한 뒤 소스 트리에 새 파일이 생기지 않아야 한다.

### 0-3. Windows에서 테스트 녹색화 · S ✅

**결과**
- 실패 2건을 고쳤다.
- 믹스인 목록은 이제 `main.py`에서 읽는다. 목록과 Api 기반 클래스 수가 일치하는지도 검사한다.
- **node**: Node.js v24 LTS를 설치해 JS 테스트 29건도 돌렸다. 처음 돌리자 1건이 실패했는데, 원인은 한글 Windows에서 node 출력을 cp949로 읽은 것이었다. UTF-8로 읽게 고친 뒤 503건이 전부 통과한다.

- **`test_log_cache.py` 2건**: 테스트 쪽을 고치는 것이 맞다(`write_bytes` 또는 `newline="\n"`). 실제 로그도 CRLF지만, 다른 경로는 이미 `splitlines()`와 `clean_terminal_log()`로 처리한다 ✔.
- **node**: node LTS를 개발 환경 요건으로 문서화한다. 대안은 Python용 JS 러너다.
- **믹스인 목록**: `tests/test_startup_imports.py:26-32`에는 16개, `main.py`에는 17개가 있다. 빠진 `api.topology_api`를 추가한다.

**검증**: Windows에서 `python -m pytest tests -q` 결과가 0 failed, 0 skipped여야 한다.

### 0-4. 테스트 데이터 격리 · S~M ✅

**결과**
- `AUTOCHECK_HOME`을 지원한다. 우선순위는 이 환경변수, 포터블, Documents 순이다.
- `conftest.py`가 어떤 앱 모듈보다 먼저 이 값을 임시 폴더로 잡는다.
- **검증**: 빈 가짜 홈(USERPROFILE)으로 전체 스위트를 돌렸다. 가짜 홈에는 아무것도 생기지 않았고, 임시 폴더도 남지 않았다.
- **바꾼 계획**: 경로 상수의 지연 계산은 하지 않았다. conftest가 모든 import보다 먼저 환경변수를 잡으므로 격리에는 필요 없다.

**현재**
- 경로 상수가 import 시점에 고정된다(`engine/project_manager.py:20-21`, `engine/customer_manager.py:8`, `engine/history.py:11` ✔).
- conftest에 격리 fixture가 없다.
- 그래서 프로젝트 폴더에서 pytest를 돌리면 실제 `Documents\AutoCheck`가 생성되거나 변경될 수 있다. 이번 실측은 포터블 사본에서 돌려 이를 피했다.

**변경**
- `core/paths.py`에서 `AUTOCHECK_HOME` 환경변수를 지원한다.
- `conftest.py`에 autouse fixture를 두어 임시 루트를 쓰게 한다.
- 경로 상수는 함수로 바꿔 지연 계산한다.

**검증**: 테스트 전후로 `Documents\AutoCheck`가 바뀌지 않아야 한다.

### 0-5. Python·의존성 정리 · S ✅

**결과**
- **의존성 검사**: 검사 로직을 `core/requirements_check.py`로 옮겼다(테스트 14건).
  - 기본 동작은 '빠졌거나 낮은 패키지와 설치 명령을 안내하고 종료'다.
  - `AUTOCHECK_AUTO_INSTALL=1`이면 예전처럼 자동 설치한다.
- **Python 버전**: 3.12 미만이면 시작할 때 안내한다(D2).
- **파일 분리**: `requirements-build.txt`를 분리하고 `requirements.lock`을 추가했다(37개, `python -m tools.lock_requirements`로 재생성).

- 지원 버전을 D2에 맞춰 문서에 반영한다.
- `pyinstaller`와 `Pillow`(빌드 전용)를 `requirements-build.txt`로 분리한다. 잠금 파일(`requirements.lock`)도 추가한다.
- `main.py`의 `ensure_requirements()`는 실행할 때마다 자동으로 pip install을 한다 ✔.
  - 사내망이나 비관리자 PC에서는 실패하거나 지연된다.
  - 검증되지 않은 최신판이 설치된다.
  - `>=` 조건은 검사하지 않는다.
  - → 누락 목록을 안내하고 종료하도록 바꾼다. 자동 설치는 개발 옵션으로만 남긴다.

### 0-6. (선택) 사전 점검 스크립트

- `tools/check.ps1` 하나로 다음 셋을 묶는다: pytest, `python -m tools.bench_log_analysis --check`, compileall.
- 원격 저장소를 쓴다면 Windows CI를 둔다.

---

## 1단계 — 보안·안전 긴급 수정 (v0.6.x)

### 1-1. 명령 카탈로그의 설정 모드 진입 제거 · S ✅

**결과**
- `core/command_policy.py`에 읽기 전용 허용 목록을 뒀다. 허용: show(sh·sho 축약 포함), terminal, enable(숫자 권한만), dir. 출력을 장비 파일에 쓰는 것(`| redirect`·`| tee`·`| append`·`> flash:`)은 막는다. 그 밖의 명령(configure·copy·write·reload·clear·no·exit/end·bash 등)은 전부 막는다.
- 세션 점검과 SSH 수집기가 위반 명령을 보내지 않고 로그·진행 상황에 남긴다. 기존 사용자 카탈로그에 켜져 있는 `conf`도 이제 장비로 나가지 않는다.
- 카탈로그: 추가·수정은 거부하고, 엑셀 가져오기·토글·초기화에서는 끄고 경고한다. 화면에 '차단' 배지와 이유를 보여준다.
- 기본 카탈로그에서 `conf`를 지우고 `show spanning-tree` 뒤 한글 오타를 고쳤다(49→48개). 엑셀 '사용여부'의 `FALSE` 문자열을 참으로 읽던 버그도 고쳤다.
- '점검 후 `end` 보장'은 하지 않았다. 설정 모드 진입 자체를 보내지 않으므로 필요 없어졌다. 랩 장비에서의 프롬프트 확인은 실장비가 없어 못 했다.
- 테스트 47건(`tests/test_command_policy.py`). 가짜 netmiko로 장비에 실제로 보내지는 내용을 검사한다.

**현재**
- `config/commands_catalog_default.xlsx`
  - 3행이 `conf`(필수·활성)다 ✔.
  - 34행은 `show spanning-tree` 뒤에 한글 오타가 붙어 있다 ✔.
- 세션 터미널 점검은 카탈로그를 작업자 세션에 그대로 보낸다(`api/terminal_inspection_api.py:63` ✔). 그래서 점검이 끝나면 작업자 터미널이 `(config)#`에 남는다.
- SSH 수집기의 별칭 목록에도 `conf`가 없다(`engine/collector.py:37` ✔).

**변경**
- 기본값을 고친다.
- 카탈로그를 저장하거나 엑셀에서 가져올 때 '읽기 전용 허용 목록'으로 검증한다. 허용: show, terminal length, enable. 설정 진입·파괴 명령은 거부한다.
- 점검이 끝나면 `end`를 보장한다.

**검증**
- `conf`, `configure`, `write erase`를 추가하면 거부되는지 테스트한다.
- 랩 장비에서 점검한 뒤 프롬프트가 특권 모드인지 확인한다.

### 1-2. 클라우드 AI 전송 보호 · S~M ✅

**결과**
- 원본로그 AI 분석은 이 PC 밖으로 나가는 요청을 보내기 직전에 `engine.log_masking.mask_for_external_ai`를 강제로 적용한다. 대상은 클라우드 요청 전부와, 다른 호스트를 가리키는 '로컬' 엔드포인트다. 비밀값·IP·MAC·계정·도메인·장비명을 가리고 명령·상태는 남긴다. 이 PC 안의 로컬 AI에는 원문이 간다.
- Gemini 키는 `x-goog-api-key` 헤더로 보낸다(분석·연결 테스트). 실패 로그에는 쿼리를 뺀 주소만 남긴다.
- `core/secret_redaction.py`: `app_logger`가 기록하는 모든 줄에서 키·토큰(URL 쿼리, Bearer, x-api-key, 제공자별 키 모양)을 지운다.
- 직접입력 엔드포인트는 https만 받는다(같은 PC의 http://localhost만 예외). 429로 빠진 파트는 결과에 '분석하지 못함'으로 남긴다.
- 테스트 21건(`tests/test_ai_payload_guard.py`). 전송 함수를 가로채 실제로 나가는 프롬프트를 검사한다.
- **미룬 것**
  - 모델 id·API 버전: `claude-sonnet-4-6`과 `anthropic-version: 2023-06-01`은 지금도 유효하다. 현행 모델로 옮기면 thinking이 항상 켜져 `max_tokens`를 다시 잡아야 하므로 4-2로 옮겼다 — 4-2에서 처리했다(기본 `claude-opus-5-5`, `anthropic-version: 2026-10-21`).
  - findings 경로(`ai_analysis/findings_analyzer.py`, `rule_engine/correlation_ai_fallback.py`)는 아직 `core/sanitizer.py`로 가리고, 복원 매핑을 CWD에 평문으로 쓴다. 채점 파이프라인(D1)에 묶인 경로라 3-1에서 `mask_for_external_ai`로 통일한다.

**현재**
- **원본로그 분석이 '마스킹 없이' 전송된다**(`ai_analysis/raw_log_analyzer.py:1` ✔, `api/log_analysis_run_api.py:1112-1141`). running-config 구간의 커뮤니티 문자열·키·호스트명·IP가 외부로 나간다.
- **Gemini 키가 로그로 유출된다.**
  - 키가 URL 쿼리에 들어간다(`raw_log_analyzer.py:99` ✔).
  - HTTP 오류가 나면 URL 전체를 print한다(`:69` ✔).
  - `core/app_logger`가 print를 로그 파일과 UI 로그로 캡처한다.
  - 결국 '전체 로그 내보내기'로 키가 나간다.
- **`core/sanitizer.py`가 docstring과 다르게 동작한다.**
  - docstring은 Password·SNMP를 가린다고 하지만(`:6-7`), 실제 코드는 가리지 않는다(`:63-73`).
  - 단어 경계 없이 치환해 토큰을 손상시킨다(`:24-34`).
  - 복원 매핑을 CWD에 평문으로 저장한다(`ai_analysis/findings_analyzer.py:31-33`).

**변경**
- 전송 직전에 마스킹을 강제하고, 우회 경로를 없앤다.
- Gemini 키는 `x-goog-api-key` 헤더로 보낸다.
- `app_logger`에 키·쿼리스트링 마스킹 필터를 단다.
- https를 강제한다(`api/cloud_ai_api.py:148`).
- 429로 빠진 청크를 결과에 명시한다(`raw_log_analyzer.py:208-246`).
- 기본 모델 id `claude-sonnet-4-6`(`raw_log_analyzer.py:78` ✔)을 현행 모델(예: `claude-sonnet-5-5`)로 바꾸고, 하드코딩 대신 설정값으로 둔다.

**검증**
- 비밀이 든 합성 로그로 전송 페이로드에 원문 비밀이 0건인지 테스트로 고정한다.
- 401·429 응답 시 로그에 키가 0건이어야 한다.

### 1-3. 웹 UI 이스케이프 통일 · M ✅

**결과**
- `core.js`의 `esc()` 하나가 `&<>"'` 다섯 글자를 모두 바꾼다. 기존 헬퍼 6종(escapeHtml·escapeAttr·rtEscape·tpEsc·dashEsc·escapeReportText)은 이를 부르는 이름만 남겼다.
- 실시간 감시 '해결/무시' 버튼이 다시 동작한다.
- 이스케이프가 빠진 화면 약 20곳(장비 목록·키 팝업·카탈로그·세션 터미널·설정·지식베이스·로그 목록·워크스페이스·대시보드 등)을 고쳤다. `"`·`&`가 든 비밀번호·명령이 저장 때 잘리던 문제도 함께 없어졌다.
- 장비가 돌려준 hostname에 호스트명에 쓸 수 없는 글자가 있으면 장비명으로 쓰지 않는다(`engine/device_probe.extract_hostname`).
- 테스트 7건(`tests/test_web_ui_escaping.py`). node로 실제 이스케이프 함수를 돌리고, 속성값 자리(`="${…}"`)의 보간이 이스케이프를 거치는지 정적으로 검사한다.
- **미룬 것**
  - CSP meta: `script-src 'self'`가 pywebview 브리지·인라인 핸들러와 충돌하는지 실제 앱에서 확인해야 한다.
  - id 목록을 data 속성 대신 JS 맵으로 참조하는 것: 이스케이프로 버튼이 복구돼 급하지 않다.
  - 비밀번호를 DOM에 넣지 않는 것: 4-1(자격증명 금고)과 함께 한다.

**현재**
- 이스케이프 헬퍼 6종이 5개 파일에 흩어져 있다. innerHTML을 쓰는 28개 파일 중 8개만 이스케이프를 한다.
- `rtEscape`·`tpEsc`는 textContent→innerHTML 방식이라 `"`를 변환하지 않는다(`web_ui/js/realtime-baseline-alerts.js:258-262` ✔, `topology.js:438-442`).

**문제 ① 기능 고장 — 실시간 감시 상세의 '해결/무시' 버튼이 동작하지 않는다 ✔**
- 버튼 속성이 `data-rtm-resolve="${rtEscape(JSON.stringify(f.alert_ids))}"`다(`realtime-monitor-panel.js:1048,1052`).
- alert_id가 `Core1#12` 같은 문자열이라(`engine/baseline_diff_engine.py:353`) 속성값이 `[`에서 끊긴다.
- 그래서 `:944`의 `JSON.parse`가 실패한다.

**문제 ② XSS — 외부 값이 이스케이프 없이 HTML에 들어간다**
- 장비명·IP·계정·위치가 `value="${…}"`에 그대로 들어간다(`inventory-table.js:62,73-80` ✔). 비밀번호도 DOM에 평문으로 들어간다.
- lemonade 모델 목록(`settings-local-ai.js:11` ✔)과 카탈로그 엑셀 값(`catalog.js:39-40`)도 마찬가지다.
- 장비명은 장비 hostname으로 자동 갱신된다(`engine/device_probe.py:29`의 `(\S+)`). 따라서 장비나 엑셀 파일이 공격 입력이 될 수 있다.
- pywebview 브리지는 평문 비밀번호 조회(`get_devices`)와, 열려 있는 SSH 세션 전체에 대한 일괄 입력(`broadcast_terminal_input`)을 노출한다.

**문제 ③ 데이터 손상**
- `"`·`&`가 든 passphrase·비밀번호·명령(`| include "…"`)이 저장할 때 잘린다.
- 위치: `inventory-key-modal.js:23,48-55`, `inventory.js:51,148-154`, `catalog.js:82`

**변경**
- `core.js`에 `esc()`·`escAttr()` 단일 헬퍼를 둔다(`&<>"'` 전부 변환). 모든 템플릿에 적용한다.
- id 목록은 data 속성 대신 JS 맵으로 참조한다.
- `index.html`에 CSP meta를 넣는다.
- 비밀번호는 DOM에 넣지 않는다(4-1과 연계).
- 이스케이프 없는 `${`를 잡는 정적 검사 테스트를 둔다.

### 1-4. 경로 경계 검사 공통화 · S ✅

**결과**
- `core/paths.py`에 세 함수를 뒀다.
  - `safe_join(base, *parts)`: 결과가 base 밖(`..`, 절대경로, 다른 드라이브)이면 ValueError.
  - `safe_child(base, name)`: base 바로 아래 한 단계 이름만 허용한다. 삭제처럼 범위가 정확해야 하는 곳에 쓴다.
  - `safe_filename(name)`: 장비명 등을 파일명 한 조각으로 다듬는다(구분자·`:`·제어문자·Windows 예약어). 정상 이름은 그대로다.
- 적용한 곳
  - 프로젝트 삭제·이름변경·활성화·내보내기. id가 `''`나 `..`이면 거부한다(예전엔 labs 전체나 사용자 데이터 폴더를 rmtree하는 경로였다).
  - 프로젝트 zip 가져오기: 모든 멤버 위치를 먼저 검사한 뒤 쓴다(zip slip이면 아무것도 쓰지 않는다). 압축 해제 크기 상한 512MB, `_`로 시작하는 내부 폴더 거부.
  - `StorageService.resolve`와 run 폴더 연산(archive/delete/move/copy), `RunManager`의 run_id
  - 지식베이스 저장·조회·삭제. 저장에는 검사가 없었고, `startswith` 검사는 옆 폴더(`knowledge2`)를 통과시켰다. 저장 실패도 화면에 '저장 실패'로 보인다.
  - 장비별 로그·점검 로그·설정 스냅샷 파일명, 화면에서 입력한 보고서 파일명
  - 채점 회차 조회·삭제는 그 랩 폴더 안의 파일만
- id·run_id는 정규식 대신 '한 단계 이름' 검사로 막았다. 기존 폴더 이름(한글·타임스탬프 등)을 그대로 쓸 수 있다.
- 테스트 64건(`tests/test_path_safety.py`).

**현재**
- `delete_project`가 id 검증 없이 rmtree한다(`engine/project_manager.py:126-130` ✔). 빈 문자열이 들어오면 labs 전체가 지워진다.
- 프로젝트 zip 가져오기에 Zip Slip 취약점이 있다(`api/project_core_api.py:146-156` ✔).
  - `proj/../../x` 같은 멤버가 labs 밖에 쓰인다.
  - 지금은 UI 진입점이 없어 잠재 위험이다(D5).
- 지식베이스 저장에 경계 검사가 없다(`api/knowledge_api.py:37-47` ✔). 조회·삭제는 `startswith` 접두 검사라 약하다.
- run_id와 장비명이 검증 없이 경로 구성요소가 된다.
  - `core/storage_service.py:82-83, 320-340`
  - `engine/run_manager.py:129-134`
  - `engine/collector.py:156-157`
  - `engine/inspection_report_builder.py:489-493`
  - `engine/history.py:69-83`

**변경**
- `core/paths.py`에 `safe_join(base, *parts)`를 둔다(resolve 후 commonpath 확인).
- id·run_id는 정규식으로 검증한다.
- 장비명은 sanitize한다(`..`, `:`, Windows 예약어).
- 모든 삭제·쓰기 경로에서 이를 강제한다.

**검증**: `..`, 절대경로, `CON`, `SW:1`, 빈 문자열을 넣는 테스트를 둔다.

### 1-5. 내보내기 재귀 팽창·자격증명 포함 · S ✅

**결과**
- 워크스페이스 내보내기가 이전 내보내기 결과(`archive/workspace_export_*.zip`, run의 `exports/`)와 쓰는 중인 임시 파일을 담지 않는다. 보관된 실행(`archive/<run_id>/`)은 이력이라 그대로 담는다. 계획의 '`archive/` 전체 제외'보다 좁게 했다.
- zip을 메모리(BytesIO)에 다 만들지 않는다. `StorageService.save_zip`이 같은 폴더의 임시 파일에 바로 압축하고 fsync 후 `os.replace`한다. 실패하면 기존 파일이 그대로 남는다. 워크스페이스·run·로그·보고서 zip에 모두 적용했다.
- 계정 정보는 기본으로 뺀다.
  - 워크스페이스: `profile/credential.json`과 `inventory/`(장비 목록 사본 — 엑셀·YAML에 비밀번호·개인키 열이 있다)를 뺀다. '전체 워크스페이스'를 고르면 '계정 정보 포함' 체크가 나오고, 포함하면 확인창과 완료 경고를 띄운다.
  - 프로젝트 zip: `device_inventory.yaml`·`ip_allocation.yaml`의 비밀번호·개인키를 비운 사본을 담는다. 계정 정보가 없는 파일은 원본 바이트 그대로다. 내보낼 때 포함 여부를 묻는다(화면 진입점은 D5 복원 후).
- 테스트 9건(`tests/test_export_safety.py`). 세 번 연달아 내보내도 크기가 같고, 기본 zip에 credential.json이 없다.
- 남은 것: 장비 목록 엑셀 내보내기(`export_devices`)는 다시 가져오기용이라 비밀번호 열을 그대로 둔다 → 4-1에서 다룬다.

**현재**
- `export_workspace`가 프로파일 전체를 rglob한다. 그래서 `archive/`에 저장된 이전 내보내기 zip까지 포함한다(`engine/report_manager.py:397-408` ✔). 내보낼 때마다 크기가 약 2배가 된다.
- zip 전체를 메모리(BytesIO)에 올린다. `export_run`·`export_logs`도 같다.
- `profile/credential.json`(평문 계정)이 들어간다. `export_project`(`api/project_core_api.py:101-123` ✔)도 같다.

**변경**
- `archive/`·`exports/`를 제외한다.
- 임시 파일로 스트리밍한 뒤 `os.replace`한다.
- 자격증명은 기본으로 제외하고, 포함할 때는 경고를 띄운다.

**검증**: 3회 연속 내보내도 크기가 일정해야 하고, zip에 credential.json이 없어야 한다.

### 1-6. 빌드 시 비밀 파일 번들 차단 · S ✅

**결과**
- `AutoCheck.spec`이 `ai_config.yaml`, `labs/`(예전 프로젝트 폴더 — 앱은 번들의 labs를 읽지 않는다), `requirements.txt`·`실행방법.txt`(소스 실행 안내)를 담지 않는다.
- 번들하기 전에 `tools/bundle_secret_check.py`로 검사하고, 문제가 있으면 빌드를 멈춘다. 검사 대상은 이름(ai_config.yaml·credential.json·portable.txt·autocheck_data), 제공자 키 모양, 설정 파일에서 값이 든 api_key·password 등의 필드다. 출력에 값은 넣지 않는다.
- 첫 실행 템플릿 복사에서 `ai_config.yaml`을 뺐다(번들에 있어도 복사하지 않는다). 없으면 앱은 빈 설정으로 시작하고 설정 화면이 만든다. 그래서 `ai_config.example.yaml`은 만들지 않았다.
- `jinja2` hiddenimport를 지우고 `upx=False`로 바꿨다(사내 백신 오탐 방지). `build_exe.py`는 jpg가 더 새로울 때만 .ico를 다시 만든다.
- 확인: spec을 가짜 Analysis로 실행했다. 그대로면 통과하고, `config/ai_config.yaml`을 넣으면 빌드가 멈춘다. 실제 EXE 빌드·실행은 하지 않았다. `unittest` exclude 의심도 EXE를 실행해 봐야 해서 5-2로 넘겼다.
- 개발 PC 영향: 저장소 루트의 `ai_config.yaml`은 더 이상 사용자 폴더로 자동 복사되지 않는다.
- 테스트 17건(`tests/test_bundle_secrets.py`).

**현재**
- 루트에 `ai_config.yaml`이 있으면 번들한다(`AutoCheck.spec:50-52` ✔).
- 번들된 사본은 첫 실행 때 사용자 폴더로 복사된다(`core/paths.py:82-88` ✔).
- 즉 개발자의 API 키가 모든 EXE 사용자에게 퍼지는 구조다. 현재 트리에는 이 파일이 없어 잠재 위험이다.

**함께 정리할 것**
- `jinja2` hiddenimport는 쓰이지 않는다.
- `unittest` exclude가 의존 패키지를 깰 수 있다(의심).
- `upx=True`는 사내 백신 오탐 위험이 있다(의심).
- 개발자용 requirements와 실행방법.txt를 동봉한다.
- `build_exe.py`가 빌드할 때마다 .ico를 소스 트리에 다시 만든다.

**변경**: `ai_config.example.yaml`만 동봉하고, 빌드 스크립트에서 비밀 패턴을 검사한다.

### 1-7. 외부 공유용 마스킹 보강 · M ✅

**결과**
- 이제 가리는 것: IPv6, `snmp-server host … <community>`(trap), JSON·대입형 API 키, 새 항목 'Auth Key'. Auth Key는 TACACS/RADIUS key(한 줄형·블록형), OSPF/NTP authentication-key, message-digest-key, key-string, VRRP/IS-IS authentication, SNMPv3 auth/priv, ISAKMP·pre-shared-key를 다룬다.
- cp949 레거시 로그를 `core/text_io`로 읽는다.
- 규칙 동작: 값 정규식이 줄을 넘지 않게 했다. 비밀값 규칙을 일반 규칙보다 먼저 적용한다(os_version이 'version 2c'를 먼저 가려 커뮤니티가 남던 문제).
- 마스킹 결과를 다시 검사해, 남은 비밀값이 있으면 화면에 경고한다(`find_unmasked_secrets`).
- 테스트 17건(`tests/test_log_masking.py`). EOS/IOS/NX-OS 문법을 섞은 설정의 비밀값 20개가 하나도 남지 않고, 줄 길이·구조가 보존되는지 검사한다.

**현재** — `engine/log_masking.py`가 놓치는 것:
- IPv6(`:21`은 IPv4만 처리)
- `snmp-server host … <community>`(`:102`)
- `tacacs-server` / `radius-server key 7`
- OSPF·NTP `authentication-key`
- `key-string`
- SNMPv3 auth/priv
- `crypto isakmp key` / pre-shared-key
- cp949 로그(`:231`)

테스트가 0건이라 회귀가 생기면 그대로 비밀이 유출된다.

**변경**
- 규칙을 확장한다.
- 마스킹 후 잔여 패턴을 스캔하고, 남은 것이 있으면 경고한다.
- EOS·IOS·NX-OS 설정으로 골든 테스트를 만든다.

### 1-8. SSH 운영 안전 · S~M ✅

**결과**
- admin/admin 폴백을 없앴다. `resolve_credentials`가 빈 계정을 채우지 않고, 새 프로젝트 기본값과 장비 목록 화면의 기본 계정 칸도 비어 있다.
- 계정이 비면 수집기·연결 확인·터미널 접속 모두 로그인 시도 없이 이유를 알린다(`credential_problem`).
- 수집기: 실패한 시도에서도 `finally`에서 세션을 닫는다. `connection.yaml`의 `ssh.connect_timeout_sec`를 `timeout`·`conn_timeout`으로 쓴다.
- 붙여넣은 개인키는 메모리에서 읽는다(`ssh_client.load_private_key_text`). 수집기도 붙여넣은 키로 접속할 수 있다. 잘못된 키는 재시도 없이 바로 알리고, 오류 문장에 키 본문을 넣지 않는다.
- 사전점검: 대표 장비가 응답하지 않으면 다른 활성 장비(최대 3대)로 다시 확인하고, 모두 응답이 없을 때만 멈춘다.
- 터미널: 접속 후 셸을 못 열면 클라이언트를 닫는다.
- 이미 저장된 장비 목록의 `default_password: admin`은 그대로 쓰인다. 실제로 쓰는 값일 수 있어 자동으로 지우지 않았다. 장비 목록의 기본 계정을 한 번 확인할 것.
- 실장비로는 확인하지 못했다(가짜 netmiko·paramiko로 검증). 테스트 19건(`tests/test_ssh_safety.py`).

**현재**
- **세션 누수**: 예외가 나면 disconnect하지 않는다. disconnect는 성공 경로(`engine/collector.py:159`)에서만 호출된다 ✔. vty가 고갈될 수 있다.
- **타임아웃 설정 무시**: `timeout=20`이 고정이라(`:98` ✔) `connect_timeout_sec` 설정이 먹지 않는다.
- **전체 중단**: 사전점검 대상 1대가 실패하면 전체 수집이 멈춘다(`:220-231`).
- **admin/admin 폴백**: 비밀번호가 비어 있으면 `admin/admin`으로 접속한다(`engine/device_inventory_core.py:33-39` ✔). N대 × 3회 로그인 시도로 AAA 계정이 잠길 수 있다.
- **개인키 평문 기록**: 붙여넣은 개인키를 %TEMP%에 평문으로 쓴다(`engine/ssh_client.py:80-88`).

**변경**
- `finally`에서 disconnect한다.
- 설정의 timeout을 사용한다.
- admin 폴백을 제거한다. 값이 비어 있으면 접속하지 않고 안내한다.
- 키는 메모리에서 로딩한다.

호스트키 검증(당시 `AutoAddPolicy`)은 4-6에서 다뤘다 — 처음 접속한 장비의 키를 기억하고 바뀌면 로그인하지 않는다.

---

## 2단계 — 데이터 무결성·판정 정확성 (v0.7.x)

### 2-A 워크스페이스·데이터

**2-1. 프로파일 생성·이름변경을 트랜잭션으로 · M**

**결과** ✅
- 이름변경(프로파일·고객사)은 data 폴더를 먼저 옮기고 그다음 메타·고객사 목록을 저장한다. 뒤 단계가 실패하면 되돌린다. 같은 고객사의 다른 프로파일과 이름이 겹치거나 그 이름의 data 폴더가 이미 있으면 거부한다.
- 대소문자만 다른 이름('ABC'/'abc')을 같은 이름으로 본다(Windows 에서 같은 폴더).
- 생성은 labs·data 를 함께 만들고 중간에 실패하면 지운다. 활성화는 둘 다 성공한 뒤에 한다. 이전 기록 폴더를 이어 쓰면 알린다.
- 활성 프로파일 이름을 바꾸면 실시간 감시가 옛 경로에 상태를 저장해 옛 폴더가 되살아나던 것을 막았다(notify_active_profile_moving).
- 미룬 것: data 폴더를 불변 프로파일 id 에 매핑(장기). 테스트 13건(`tests/test_profile_transactions.py`).

현재 문제(`api/customer_profile_api.py`):
- 이름변경: 메타를 먼저 저장한 뒤, 폴더 rename이 실패해도 그대로 반환한다(`:218-224`).
- 생성: 중복 검사가 없고 FileExistsError를 무시한다(`:309-322`).
- `rename_customer`: 중간에 실패하면 불일치가 남는다(`:126-137`).

결과: 이미 있는 이름으로 바꾸면 두 회차가 같은 data 폴더를 공유한다. 로그·보고서·스냅샷이 섞이고, 원래 폴더는 고아가 된다.

변경:
1. 사전에 중복을 검사한다.
2. 폴더 rename이 성공한 뒤에 메타를 저장한다.
3. 실패하면 롤백한다.
4. 장기적으로는 data 폴더를 불변 프로파일 id에 매핑한다.

**2-2. 한글 고객사 공용 카탈로그 충돌 · S**

**결과** ✅ — 고객사 공용 폴더를 `labs/_customers/<customer_id>` 로 바꿨다. 예전 위치(이름 슬러그)의 카탈로그는 접근할 때 각 고객사로 한 번 복사한다(UPGRADE_FUNCS 대신 — 2-3 참고). id 가 없는 예전 메타는 예전 위치를 그대로 쓴다. 테스트 7건(`tests/test_customer_catalog_paths.py`).

- `_slugify`가 비ASCII 문자를 지운다. 결과가 비면 `"project"`를 쓴다(`engine/project_manager.py:33-35` ✔).
- 그래서 모든 한글 고객사가 `labs/_customers/project/`(`:204` ✔)를 공유한다. 'NC서비스망'과 'NC소프트'는 둘 다 `nc`가 된다.
- 결과: A사 카탈로그를 고치면 B사의 점검 명령이 바뀐다.
- 변경: 경로 기준을 customer_id로 바꾸고, `UPGRADE_FUNCS`에 1회 이전 함수를 추가한다.

**2-3. 사용자 설정 업그레이드 체계 · M**

**결과** ✅
- `core/app_config.py`: 출처 정책의 단일 출처. 앱 소유 규칙(log_rules.json, 기본 카탈로그 xlsx)은 번들에서만, 사용자가 고칠 수 있는 템플릿(inspection_report_template.yaml)은 번들 기본값 위에 사용자 사본을 얹는다(check_items 는 name 으로 빠진 필드만 채운다 — virtual_na 가 기존 사용자에게도 적용된다).
- 첫 실행 복사는 고칠 수 있는 템플릿만, 폴더가 있어도 빠진 템플릿은 채운다.
- 업그레이드 단계(`apply_pending_upgrades`)를 레거시 마이그레이션과 분리해 시작할 때마다 확인한다. 단계마다 기록하고 실패하면 다음 시작에 다시 시도한다. 등록된 단계는 아직 없다.
- 버전 파일을 원자적으로 쓰고 다른 키를 지우지 않는다. 되돌리기는 복사한 뒤 손대지 않은 파일만 지운다.
- 테스트 10건(`tests/test_config_upgrade.py`).

현재:
- **설정이 갱신되지 않는다.** `config/`는 사용자 폴더에 '없을 때 1회'만 복사된다(`core/paths.py:91-100` ✔).
- **신기능이 적용되지 않는다.** 보고서 템플릿은 이 사용자 사본을 읽는다(`report/inspection_excel.py:60` ✔). 그래서 v0.5.9 이전에 설치한 사용자에게는 `virtual_na`(가상환경 → 해당없음)가 적용되지 않는다.
- **출처 정책이 섞여 있다.** `log_rules.json`은 번들에서 읽는다(`engine/log_rule_engine.py:83-85` ✔). 파일마다 읽는 곳이 다르다.
- **업그레이드 함수가 거의 실행되지 않는다.** `UPGRADE_FUNCS`는 레거시 데이터가 있을 때만 돈다(`engine/migration_manager.py:97-103, 271-275, 362-367`).
- **버전 파일 쓰기가 비원자적이다**(`:74-78`).
- **롤백이 과격하다.** 복사된 폴더를 통째로 rmtree한다(`:328-336`).

변경:
- '번들 기본값 + 사용자 오버라이드 병합' 원칙으로 통일한다.
- 마이그레이션과 분리된, 앱 버전 기반의 업그레이드 단계를 둔다.
- 버전 파일은 원자적으로 쓴다.

**2-4. Run 관리 견고화 · S**

**결과** ✅
- 깨진 session.json 은 목록에 CORRUPTED 로만 보이고, metadata·health·보고서 목록을 못 읽어도 그 칸만 비운다.
- 앱 시작 때 RUNNING/PAUSED 로 남은 run 을 ABORTED 로 정리한다(`recover_all_incomplete_runs` — main.py 에서 호출, 폴더를 만들지 않는다).
- run 폴더를 `mkdir(exist_ok=False)` 재시도로 만든다(같은 초의 두 실행이 한 폴더를 쓰지 않게).
- CRT 인제스트는 전용 run(crt_ingest)에 담는다. '최신 run'(기준·구성도·보고서 위치)은 점검 run 을 우선한다.
- 진입점 통합은 하지 않았다(RunManager.create_run 이 이미 유일한 경로). 테스트 8건(`tests/test_run_robustness.py`).

현재:
- session.json 하나가 손상되면 Workspace 탭 전체가 오류가 난다(`engine/run_manager.py:196-208`).
- 비정상 종료된 run은 영구히 RUNNING으로 남아 '현재 Run'에 고착된다(`:210-248`). `recover_incomplete_runs`는 어디에도 연결돼 있지 않다.
- CRT 인제스트가 최신(이미 완료된) run에 섞인다(`api/log_file_browser_api.py:72-83`). WORKSPACE_STRUCTURE의 '1점검 = 1run' 원칙에 어긋난다.

변경:
- 예외를 포괄적으로 잡고, 손상된 run은 표시만 한다.
- 시작 시 복구를 호출한다.
- `create_run` 진입점을 하나로 합치고 `mkdir(exist_ok=False)`를 쓴다.
- 인제스트할 때마다 새 run을 만든다.

**2-5. 삭제를 보관으로 · M**

**결과** ✅ — 삭제(고객사 트리·프로젝트 모달)와 워크스페이스 '보관'이 같은 보관 경로를 쓴다(`engine/profile_archive.py`): labs·data 를 `data/<고객사>/_archived_profiles/<프로파일>_<시각>/` 로 함께 옮기고 archive.json 을 남긴다. 복원·영구 삭제는 API(`list/restore/purge_archived_profile`)로 둔다. 고객사 삭제는 프로파일을 모두 보관하고 목록에서만 뺀다. 보관함 화면은 4-10. 테스트 9건(`tests/test_profile_archive.py`).

현재:
- 삭제 경로가 2벌이고, 둘 다 영구 rmtree다(`api/customer_profile_api.py:247-258`, `api/project_core_api.py:82-99` ✔).
- 고객사를 삭제하면 `_archived_profiles`도 함께 지워진다(`engine/profile_manager.py:356-359`).
- 보관은 labs 프로젝트와 활성 상태를 그대로 둔다(`api/workspace_api.py:222-228`).
- 복원 기능이 없다.

변경: 보관을 기본 동작으로 하고, 복원과 영구삭제를 분리한다. UI는 4-10의 보관함에서 만든다.

**2-6. 인벤토리 왕복 손상 · S**

**결과** ✅ — `normalize_device` 가 enabled 를 bool 로('FALSE'/0/no/미사용), tag 를 리스트로, 포트를 정수로 맞춘다(이미 문자열로 저장된 목록도 불러올 때 고쳐진다). 덮어쓰기 가져오기는 파일에 실제로 값이 있던 칸만 바꾼다. 테스트 18건(`tests/test_inventory_roundtrip.py`).

- **비활성 장비가 수집 대상이 된다.** 엑셀 내보내기는 `enabled`를 문자열 `"FALSE"`로 쓰는데, 가져올 때 이 값을 참으로 처리한다(`engine/device_inventory_import_export.py:117`, `device_inventory_core.py:124-126`).
- **기존 키가 지워진다.** overwrite 시 빈 `key_content`가 기존 키를 덮어쓴다(`:58-61`).

**2-7. 조회 경로에서 쓰기 제거 · M**

**결과** ✅ — 고객사 트리는 고객사 id 가 실제로 바뀌거나 profile_name 이 비었을 때만 메타를 쓴다. 깨진 메타·모르는 mode 는 그 프로젝트만 건너뛴다. `list_profiles` 는 읽기만 하고 '_' 폴더를 건너뛴다. `project_paths()` 는 옮길 계정이 있을 때만 data 폴더를 만든다. 보고서 브랜딩은 settings.json 을 읽기만 한다. 테스트 6건(`tests/test_read_paths_do_not_write.py`).

현재:
- 캐시가 빗나갈 때마다 모든 `project_meta.yaml`·`customers.yaml`을 다시 쓴다(`api/customer_profile_api.py:63-69`).
- `_archived_profiles`까지 repair해서 가짜 프로파일을 만든다(`engine/profile_manager.py:395-404`).
- 조회 중에 폴더를 생성한다(`engine/report_manager.py:133-135`, `engine/project_manager.py:195-228`).
- YAML 하나가 손상되면 트리 전체가 실패한다(`engine/project_manager.py:68-76`).

결과(의심): 병렬 호출 시 이름변경이 유실될 수 있고, Windows에서는 PermissionError가 날 수 있다.

### 2-B 실시간 감시 정확성

가장 활발히 쓰이고 개선되는 기능이다. 각 항목은 재현 테스트를 먼저 쓴다. 대부분은 지금 테스트가 없다.

**2-8. 상태 영속화 · S**

**결과** ✅ — 10초로 묶인 저장 요청은 버리지 않고 후행 저장한다. 창을 닫을 때 남은 변화를 저장한다(`window.events.closing`). 다른 날 저장본에서 되살린 시각에 'MM-DD ' 를 붙인다. 깨진 상태 파일은 `.corrupt-<시각>` 으로 보존하고 `.bak` 한 세대로 복원한다. 테스트 7건(`tests/test_realtime_state_persistence.py`).

현재:
- 10초 안에 들어온 저장 요청은 **버려지고**, 나중에 다시 저장하지 않는다(`api/log_analysis_run_api.py:149-162` ✔).
- 창을 닫을 때 저장하는 훅이 없다(`main.py` ✔).
- 결과: 경고 직후 5초 안에 자동 복구된 상태가 저장되지 않으면, 재실행 때 CRITICAL이 미해결로 복원된다.
- 저장된 ts에 날짜가 없다.
- 손상된 상태 파일을 보존하지 않고 덮어쓴다(`engine/realtime_monitor.py:1219-1230`).

변경:
- dirty 플래그와 후행 저장을 둔다.
- `window.events.closing`에서 강제 저장한다.
- ts에 날짜를 포함한다.
- 손상 파일은 `.corrupt-<시각>`으로 보존하고, `.bak` 한 세대를 유지한다.

**2-9. 판정 일관성 · M**

**결과** ✅ — 체크리스트 상태를 항목에 쌓인 가장 심각한 판정으로 정하고, 복구형 경고는 상태·alert_ids 를 바꾸지 않는다. 재시작 뒤 같은 사건을 새 id 로 다시 판정하면 그 id 를 복원된 경고에 잇는다(해제가 따라간다). 상한은 해제·무시된 것과 덜 심각한 것부터 뺀다. `show logging` 출력은 past_record 로 이력에만 남긴다(체크리스트·집계·syslog 관측 제외). 같은 줄을 diff 엔진이 판정했으면 규칙 경고를 버린다. 숨김·고정 필터는 프로파일(id)별. CRT 감시기는 감시 시작 때의 크기까지를 이력으로 읽는다. 테스트 12건(`tests/test_realtime_consistency.py`).

- **fail → warn 강등**: 뒤따른 경미 경고 하나에 fail 항목이 warn으로 바뀐다(`engine/realtime_monitor.py:390` ✔).
  - status를 '방금 들어온 경고'의 심각도로 계산하기 때문이다.
  - 복구형 경고도 alert_ids에 섞인다(`:392`).
- **재시작 후 복원된 경고가 자동 복구되지 않는다.**
  - seed가 새 id로 condition을 연다.
  - 복원본은 history 중복으로 버려진다(`realtime_monitor.py:279-284, 710-760`, `baseline_diff_engine.py:485-512`).
- **두 엔진이 같은 syslog를 중복 경고한다.** 그중 규칙 스트림 경고는 영영 해제되지 않는다(`component_id=None`: `engine/realtime_rule_stream.py:140`, `api/log_analysis_run_api.py:943-949`).
- **`show logging` 출력(history)이 실시간 이벤트로 집계된다.** 토스트·live 경고·syslog 관측으로 모두 잡힌다(`log_analysis_run_api.py:974`, `realtime_rule_stream.py:64-89`, `baseline_diff_engine.py:575-580`).
  - 며칠 전 로그가 CRITICAL로 쏟아진다.
  - syslog를 받지 못하는 장비가 '정상'으로 풀린다.
- **경고 300건 상한**: 오래된 것부터 무조건 축출한다(`realtime_monitor.py:291-292`). 미해결 CRITICAL이 패널에서 사라지고, 체크리스트의 fail은 고착된다.
- **필터가 전역이다.** 숨김·고정 필터가 앱 전체에 적용돼, 고객사 A에서 숨긴 장비가 B에서도 숨겨진다(`log_analysis_run_api.py:721-722`).
- **과거 파일 재판정**: 처음에 최신이 아니던 기존 파일이 나중에 갱신되면, 파일 전체를 live로 다시 판정해 토스트가 폭주한다(`core/crt_stream_watcher.py:142-156, 216-218`).

**2-10. 명령·프롬프트 인식 · S~M**

**결과** ✅ — `int`/`shut`/`no shut`/`no int` 축약을 받는다. Cisco '(config-if)' 는 명령으로 잡은 문맥을 유지한다. 프롬프트가 다른 인터페이스를 보여 주면 프롬프트를 따른다. 파일명 토큰 구분자에서 '-' 를 뺐다(호스트명의 일부). 미룬 것: 프롬프트 정의 통합(3단계), 줄머리 타임스탬프(의심). 테스트 11건(`tests/test_realtime_command_recognition.py`).

- **축약 명령 미인식**: `shut`, `no shut`, `int et2`를 놓친다. 또 이미 문맥이 있으면 프롬프트를 무시한다(`engine/baseline_diff_engine.py:146-148, 632-636`).
- **Cisco 문맥 소실**: `(config-if)#`가 인터페이스 문맥을 지운다(`:94, 641-642`). 그래서 IOS/NX-OS에서는 shutdown을 추적하지 못한다.
- **프롬프트 정의가 3곳에서 다르다**(`baseline_diff_engine.py:91`, `log_rule_engine.py:147`, `stream_device_matcher.py:25`). 하나로 통합한다(3단계와 연계).
- **파일명 오매칭**: 파일명을 하이픈으로 쪼개 부분 일치로 장비를 판정한다. 그래서 `Core1-B_x.log`가 Core1로 귀속된다(`engine/stream_device_matcher.py:26, 72-75`).
- **(의심)** SecureCRT의 줄머리 타임스탬프 로그를 지원하지 않는다.

**2-11. 오탐·미탐 패턴 · S~M**

**결과** ✅(코드로 확인할 수 있는 것) — EOS BGP 'new state' 문구, Cisco %LINK-3-UPDOWN, NX-OS %ETHPORT 를 잡는다. 규칙 routing_adjacency_lost 가 방향(OSPF to DOWN/INIT, BGP Down·new state Idle 등)을 본다. `copy run start`·`reload cancel` 은 위험 명령이 아니다. MLAG Disabled 는 이상이 아니다. 묶어 낸 DOWN 은 묶인 포트가 모두 올라오면 해제된다. 실기 코퍼스 검증은 남았다. 테스트 19건(`tests/test_realtime_patterns.py`).

실기 로그 코퍼스가 있어야 검증할 수 있다.

- **미탐**
  - 실기 EOS BGP 문구(`old state Established … new state Idle`)에는 Down/Up 단어가 없어 검출되지 않는다(`baseline_diff_engine.py:183`)(의심).
  - ADJCHG 서명(`log_rules.json:253`)이 ADJCHANGE를 잡지 못한다.
  - Cisco `%LINK-3-UPDOWN`, NX-OS ETHPORT에 대응하지 않는다.
- **오탐**
  - OSPF 인접 '수립'도 방향을 보지 않고 CRITICAL로 잡는다.
  - `copy run start`·`reload cancel`을 위험 명령으로 분류한다(`baseline_diff_engine.py:156`).
- **상태 폴링**
  - MLAG Disabled(미구성)를 split-brain CRITICAL로 판정한다(`engine/state_poller.py:351-352`).
  - 병합된 DOWN 경고는 영구히 해제되지 않는다(`:229-240`).

**2-12. 규칙 파일 운영 · S**

**결과** ✅ — 판정 캐시 키에 규칙 파일 신원(경로·수정시각·크기)을 넣었다. `get_engine()` 은 규칙 파일이 바뀌면 다시 만든다. 실시간 규칙 스트림은 엔진을 붙들지 않는다. JSON 오류는 앱 로그와 감시 시작 경고로 드러낸다(`rules_status`). 테스트 4건(`tests/test_rule_file_operations.py`).

현재:
- **규칙을 바꿔도 옛 판정이 나온다.** 다시 읽어도 판정 캐시가 옛 결과를 돌려준다. 캐시 키가 (경로, mtime, 크기)뿐이기 때문이다(`engine/log_cache.py:47-53` ✔, `engine/log_analysis.py:219, 231`).
- **오류가 조용히 묻힌다.** JSON 오류가 나면 조용히 기본값을 쓴다(`engine/log_rule_engine.py:92-96` ✔). 쉼표 하나 때문에 서명 전부가 사라져도 아무 표시가 없다.
- **실시간 쪽은 재로드가 안 된다.** 실시간 스트림은 엔진을 고정해 두어 재로드를 반영하지 않는다(`realtime_rule_stream.py:103-110`).

변경:
- 캐시 키에 규칙 세대를 넣는다.
- 규칙 로드 결과와 오류를 UI에 표시한다.
- 실시간 스트림도 재로드를 반영하게 한다.

**2-13. 동시성 · S** (발생 확률은 낮다)

**결과** ✅ — 감시·폴링 콜백을 한 파이프라인 잠금 안에서 돌린다. 세대 토큰으로 전환 중 늦게 도착한 결과를 버린다. `observations()` 는 잠금 안에서 복사한다. 감시기 stop() 은 끝나지 않은 스레드를 놓지 않는다. 테스트 4건(`tests/test_realtime_concurrency.py`).

현재:
- drain이 경쟁하고, 반영이 비원자적이다(`log_analysis_run_api.py:498-505, 943-970`).
- `observations()`가 잠금 없이 순회한다(`baseline_diff_engine.py:559-563`).
- watcher join이 타임아웃된 뒤에도 스레드가 남는다(`crt_stream_watcher.py:109-114, 321-323`).
- (의심) 프로파일 전환 중에 이전 세대의 결과가 섞일 수 있다.

변경:
- 파이프라인 락을 둔다.
- analyze가 (alerts, resolutions)를 함께 반환하게 한다.
- 세대 토큰을 쓴다.

### 2-C 보고서

**2-14. 보고서 데이터 보호 · S**

**결과** ✅ — 같은 이름이 있으면 '_v2' 로 저장한다. PDF 는 만든 뒤에 지원이력을 기록한다(표에는 메모리로 넣는다). 이력·스냅샷을 원자적으로 쓰고, 깨진 이력은 `.corrupt-<시각>` 으로 보존한다. '전월'은 이번 점검일보다 앞선 회차 중 가장 최근 것을 고른다(스냅샷에 점검일을 남긴다). 테스트 6건(`tests/test_report_data_protection.py`).

- **덮어쓰기**: 당일 날짜 파일명으로 저장한다(`engine/inspection_report_builder.py:483-498`). 수기로 보완한 보고서가 경고 없이 사라진다.
- **기록 순서**: PDF를 만들기 전에 이력부터 기록한다(`:534-539`).
- **이력 소실**: 이력을 비원자적으로 쓰고, JSONDecodeError가 나면 `[]`로 간주해 덮어쓴다(`:123-135, 167-192`). 고객사 누적 지원이력이 사라질 수 있다.
- **전월 오류**: '전월' 스냅샷을 saved_at이 가장 최신인 것으로 고른다(`:138-156`). 그래서 10월 보고서를 재생성하면 11월 값이 들어간다.

**2-15. 렌더러 결함 · S**

**결과** ✅ — `ptext()` 로 PDF 데이터 자리를 이스케이프하고 줄바꿈은 <br/> 로 바꾼다(정기점검 PDF·pdf_report.py). 폰트는 앱 폴더 fonts/ → %WINDIR%\Fonts → 사용자 폰트 폴더 순으로 찾고, pdf_report.py 도 한글 폰트를 쓴다. HTML 리포트 헤더를 이스케이프한다. 로그 파일명은 날짜·시각이 숫자일 때만 규칙 이름으로 본다. 미룬 것: 나눔고딕 번들(폰트 파일 확보 필요). 테스트 9건(`tests/test_report_renderers.py`).

현재:
- **PDF 생성 실패**: Paragraph에 원문을 그대로 넣는다(`report/inspection_pdf.py:302, 322, 336, 364, 372, 404`). 'Core<->Agg'나 'A&B'가 있으면 생성이 실패한다.
- **폰트 문제**
  - 폰트 경로가 `C:/Windows/Fonts`로 고정돼 있다(`:42-47`).
  - `report/pdf_report.py`는 한글 폰트를 등록하지 않아 □로 나온다.
- **HTML 헤더 미이스케이프**: `engine/report_manager.py:169`.
- **장비명 오해석**: 로그 파일명 파서가 밑줄이 3개 이상인 장비명을 잘못 읽는다. `SEOUL_CORE_SW_01`이 `01`이 된다(`core/log_naming.py:65-68`).

변경:
- 공용 escape를 만든다(`\n`→`<br/>` 포함).
- 나눔고딕(OFL)을 번들한다.
- 파일명의 날짜·시각 토큰이 숫자인지 검증한다.

**2-16. 구성도 · S**

**결과** ✅ — 캐시를 복사한 사본에 실시간 상태를 얹는다. 장비마다 모든 점검 run 중 가장 최근 로그를 쓴다(CRT 인제스트 run 제외). 배치는 프로파일 id 키로 저장하고 예전 이름 키를 읽는다. 계층 키워드는 이름 부분의 시작에서만 맞추고 긴 키워드부터 본다. 테스트 10건(`tests/test_topology_robustness.py`).

- **낡은 상태가 남는다.** 캐시 객체를 실시간 오버레이가 직접 수정한다(`api/topology_api.py:28-34, 166-168`). 그래서 감시를 끈 뒤에도 마지막 상태가 그대로다.
- **최신 run 하나만 본다.** 최신 run의 raw만 사용한다(`:151-157`). 1대만 재점검하면 구성도 대부분이 사라진다.
- **배치 유실**: 배치를 전역 config에 프로파일 이름 키로 저장한다(`:251-261`). 이름을 바꾸면 배치가 사라진다.
- **계층 오분류**: 계층 키워드를 부분문자열로 매칭한다(`engine/topology_builder.py:41-46`). 예를 들어 'tor'가 'storage'에 걸린다.

### 2-D UI 오류 처리

**2-17. API 응답 규약과 예외 기록 · M**

**결과** ✅(일부) — Api 의 공개 메서드를 감싸 예외를 traceback 과 함께 앱 로그에 남긴다(`api/api_logging.py`, 원래 시그니처 보존 — pywebview 가 getfullargspec 으로 읽는다). 화면 JS 예외도 `log_client_error` 로 남긴다. 지적된 null 역참조 4곳, '저장됨' 오표시, 확인 없는 삭제 3곳, 다른 탭에서 멈추던 점검 진행 폴러, 화면 전환 경쟁(한 번에 하나, 마지막 것만), 실시간 패널의 겹친 폴링·중복 줄을 고쳤다. 미룬 것: 반환 형태 {ok, data, error} 통일, 공용 확인 모달, 폴러의 createAdaptivePoller 통일. 테스트 16건(`tests/test_api_error_logging.py`) + 델타 병합 1건.

현재:
- **반환 형태가 제각각이다**: str 또는 ValueError, bool, `{success, reason}`, `None/{error}/{ok}`.
- **서버 기록이 없다.** `call()`은 토스트만 띄운다(`web_ui/js/core.js:64-76`).
- **null 역참조**: `call()`이 null을 돌려준 뒤 그대로 역참조한다. 감시 토글 버튼이 disabled로 고착된다.
  - 위치: `realtime-baseline-alerts.js:273-275`, `realtime-monitor-panel.js:256-261`, `settings-cloud-api.js:171-173`, `settings-local-ai.js:64-66`
- **저장·삭제 처리**
  - 저장에 실패해도 '자동저장됨'을 띄운다(`inventory.js:19-21`, `catalog.js:83,143,188`, `knowledge.js:58`).
  - 확인 없이 삭제한다(`knowledge.js:64-66`, `catalog.js:73-78`, `settings-cloud-api.js:176-177`).
- **진행 폴러 예외**: 점검 진행 폴러가 다른 탭에 있으면 매초 예외를 낸다(`connection-inspection.js:65`, `connection.js:150`). 자동 세션 닫기와 완료 알림이 늦어진다.
- **렌더 경쟁**
  - 내비게이션에 세대 토큰이 없다(`core-navigation.js:38-65`).
  - 0.8초 폴링에 in-flight 가드가 없고, seq 중복 제거 없이 append한다(`realtime-monitor-panel.js:342-353, 409`).

변경:
- `@api_endpoint` 데코레이터를 둔다. 예외를 traceback과 함께 `app_logger`에 기록하고 `{ok:false, error}`로 반환한다.
- `call()` 규약을 `{ok, data, error}`로 정한다.
- 공용 확인 모달을 만든다.
- 내비게이션에 세대 토큰을 둔다.
- 폴러는 `createAdaptivePoller`로 통일한다.

---

## 3단계 — 구조 정리 (v0.8.x)

### 3-1. D1 실행 — 채점 파이프라인 퇴역(권장안) · M~L

**결과** ✅ — UI 진입점(`api/grade_api`·`api/report_api`)과 `engine/grading`·`engine/scheduler`·`config/scheduler.yaml`을 지우고, main 에서 닿지 않게 된 모듈 68개를 import 그래프로 확인해 삭제했다(`pipeline/`·`rule_engine/`·`alarm/`·`plugins/`·`core/ai/`·채점용 파서 9개·findings 분석·리포터 플러그인·PPTX 등). 대시보드는 항상 점검 로그 기반 KPI. python-pptx 의존성 제거. 1단계에서 넘어온 'findings 경로 마스킹 통일'은 경로 자체가 없어져 해소. 무인 정기점검 재작성은 4-3으로 넘겼다(아래 스케줄러 결함 목록과 GUI 활성 프로젝트 보호를 4-3 요구사항으로) — 그때 쓸 수집 체인(`engine/collector`·`session_timeline`·`event_extractor`·`core/event*`·`progress_engine`·`structured_log`)은 테스트와 함께 남겼다.

**무인 정기점검 재작성**(4-3과 연계 — 4-3 에서 처리 ✅): `engine/scheduler.py`를 '수집 → 로그분석 → 정기점검 보고서'로 다시 짠다. 지금 스케줄러의 문제는 다음과 같다.
- CWD 상대 경로라 설치본의 설정을 읽지 못한다(`:17`).
- `*/5`, `1-5` 같은 cron 표현이 오류 없이 무시된다(`:20-23`).
- 실행 중 시각이 도래한 job이 누락된다(`:83-93`).
- 실패 통보가 print뿐이고, 결과가 None이어도 성공으로 처리한다(`:75-79`).

**GUI 활성 프로젝트 보호**
- 스케줄러가 실행되면 `set_active_project()`로 GUI 상태를 바꾼다(`engine/grading.py:46-47` ✔).
- 실행 컨텍스트를 인자로 넘기고, 프로젝트별 파일 락을 둔다.

**제거 후보** — 참조가 0인지 확인한 뒤 삭제한다.
- `pipeline/`
- `rule_engine/`(rules·registry·correlation_*)
- `engine/comparators/`, `engine/scorer.py`
- 채점용 `engine/history.py`
- `engine/report/`(외부 import 0건)
- `core/root_cause.py`, `engine/network_discovery.py`
- `core/ai/`(mock, 호출 0건)
- `web_ui/js/collection.js`의 채점 화면, `api/grade_api.py`

재건을 택한다면 부록 B의 목록을 따른다.

### 3-2. 죽은 코드·미사용 API 정리 · M

**결과** ✅ — 화면에 노출되는 API 165개 → 135개: 원본 화면도 부른 적 없는 14개 삭제(연결 설정·EVE 탐색 잔재·CRT 인제스트 등), Python 만 부르는 16개는 `_` 접두로 숨김. `tests/test_api_surface.py`가 '공개 메서드 = 화면이 가리킴(아니면 RESERVED 에 이유)'과 '화면 호출 = 존재'를 지킨다(RESERVED: 보관함 API 3개 → 4-10). 인제스트 코드와, 그런 run 이 생길 수 없던 2-4 의 crt_ingest 구분을 함께 지웠다. 미연결 매니저 메서드(후속과제 #3)와 이름이 어디에도 안 나오던 함수 40여 개 삭제 — LogManager 는 `list_logs`·`save_raw_log`만 남았다. 수집 체인(4-3)의 미사용 함수 3개와 지원용 `forget_ensured`·`memo_stats`·`rollback_migration`은 남겼다.

- JS와 Python 어디서도 쓰지 않는 API가 18개다(`save_catalog_full`, `get_connection_settings`, `run_grade_async`, `cancel_job`, `generate_report_as` 등).
- `api/report_api.py`의 공개 메서드 9개는 UI 호출이 0건이다.
- `api/log_analysis_run_api.py:36-105`의 인제스트 코드는 호출이 0건이다.
- 미연결 매니저 메서드(Known follow-up #3)는 연결하거나 삭제한다.
- pywebview에 공개된 메서드가 약 180개다. 내부용 메서드는 `_` 접두로 숨기고, 노출 목록을 테스트로 고정한다.

### 3-3. 보고서 경로 일원화 · M

**결과** ✅ — `build_context()` + 포맷 레지스트리(`register_format`: Excel·PDF) + `export()` 하나가 저장 위치(프로파일 `reports/`)·이름 다듬기·`_v2` 회피·다음 회차 스냅샷을 모든 포맷에 똑같이 맡는다. 워크스페이스 '리포트 생성'도 같은 경로(채점 데이터로 빈 보고서를 만들던 것 퇴역), '최근 리포트'·리포트 수는 보고서 탭과 같은 목록. 채점 렌더러(`report/markdown·html·pdf·excel·inspection_report`)·`core/health_score`·`core/finding`·python-docx 퇴역 — ReportManager 는 내보내기만 맡는다. 테스트 `tests/test_report_formats.py` 외.

**현재**
- 보고서 파이프라인 4벌이 공존한다. UI가 실제로 쓰는 것은 그중 2벌이다.
- Workspace의 '리포트 생성'은 `scored=[]`로 빈 보고서를 만든다(`api/workspace_api.py:283-287`, `engine/report_manager.py:147-151`).
- '최근 리포트'에 정기점검 보고서가 보이지 않는다(`workspace_api.py:252-257`).
- 포맷별 분기를 직접 해서 레지스트리 원칙에 어긋난다(`report_manager.py:153-192`).

**변경**
- `build_context()`를 표준 데이터 모델로 삼는다.
- 렌더러는 레지스트리로 관리한다.
- 저장 위치를 하나로 합친다.

### 3-4. 작업 관리 일원화 · M

**결과** ✅ — JobRunner 에 협조형 취소·대기열·진행 로그·완료 이력을 넣고, 세션 점검을 JobRunner 로 옮겼다('작업 함수 예외 뒤 영구 진행 중' 결함 해소, 화면이 완료·중지·오류를 구분). 워크스페이스 보고서는 대기열로 차례로 만들고 완료 알림은 완료 이력으로 찾는다. `core/job_manager`·`history_manager`는 3-1 에서 퇴역. 미룬 것: 분석 작업의 취소 연결·작업 센터 화면(→4-9), 수집기의 `progress_engine` → JobRunner(→4-3). 테스트 `tests/test_job_runner.py`·`tests/test_terminal_inspection_jobs.py` 외.

- 작업 관리가 세 갈래다. 이를 JobRunner 하나로 합치고 취소와 큐를 지원한다.
  - `api/job_runner.py`
  - `core/job_manager.py` + `history_manager` + `progress_engine`(UI 호출 0)
  - `terminal_inspection_api`의 자체 관리
- `run_grade_async`의 문제(D1에 따라 함께 정리한다):
  - 락이 누수된다(`api/grade_api.py:55-60`, `core/job_manager.py:84-85`).
  - 남이 만든 run을 archive로 옮긴다(`grade_api.py:69-79`).

### 3-5. `api/log_analysis_run_api.py` 분해 · M

**결과** ✅ — 1,221줄 → 분석 실행만 179줄. 감시 상태·로직은 `engine/realtime_watch_service.py`(`RealtimeWatchService`·`RealtimeWatchConfig`), `api/realtime_watch_api.py`는 같은 이름으로 위임만 한다(조합 순서 유지). 설정 읽기-수정-쓰기, 프로파일 전환 재진입(플래그→잠금), 상태 저장·폴더 이동, 세대 올리기, 지연 생성을 잠금으로 묶었다 — 잠금을 빼면 설정 갱신 5회 시도에 55건 유실(Windows 에선 동시 `os.replace` PermissionError), 전환 재시작 4번으로 테스트가 실패함을 확인. 테스트 `tests/test_realtime_watch_service.py`.

- 이 파일은 1,176줄이다. OPTIMIZATION_PLAN 당시(910줄)보다 늘었고, 관심사 6개가 섞여 있다.
- 믹스인 조합 순서는 그대로 두고, 로직을 `RealtimeWatchService` 순수 클래스로 위임한다.
- YAML 읽기-수정-쓰기에 잠금을 넣는다(`:740-757`).
- 플래그 대신 락을 쓴다(`:204-240`).
- OPTIMIZATION_PLAN이 비목표로 둔 것은 '성능 사유의 분리'다. 이 항목은 유지보수 사유다.

### 3-6. 계층 규칙 복구 · M

**결과** ✅ — 역방향 import 3갈래 제거: `core/workspace_model.py`(Profile·RunHandle·RUN_SUBDIRS), `core/log_naming.py`(점검 로그 판별·CRT 세션 로그 장비명 — 두 장비명 함수는 경고대로 따로 둔다), `engine/inspection_evaluator.py`(항목 판정 — 엑셀 렌더러 742→317줄, 구성도 API 가 openpyxl 을 끌어오지 않게 됨). `history_manager`는 3-1 에서 삭제돼 옮길 것이 없었다. `tests/test_layering.py`가 함수 안 import 까지 방향(api → engine/ai_analysis → report/parsers → core)을 지킨다.

- core가 engine을 역방향으로 import한다(`core/storage_service.py:29`, `core/history_manager.py:18-19`, `core/crt_stream_watcher.py:346`).
- report도 engine을 import한다(`report/inspection_excel.py:202`).
- 따라서 ARCHITECTURE.md의 "역방향 의존 없음" 서술은 사실과 다르다.
- 변경:
  - `Profile`·`RunHandle`·`RUN_SUBDIRS`를 `core/workspace_model.py`로 옮겨 지연 import(순환)를 없앤다.
  - `history_manager`는 engine으로 옮긴다.
  - 의존 방향을 테스트로 고정한다. `tests/test_text_io.py`에 api import를 검사하는 선례가 있다.

### 3-7. 프런트엔드 구조 · M

**결과** ✅ — 화면 수명주기(`onPageLeave`/`leaveCurrentPage`): 전체 로그 탭 인터벌, 장비 목록·구성도의 전역 리스너 누적을 고쳤고 화면 폴러는 떠나는 즉시 멈춘다. 진행 폴러는 브리지 준비 뒤 시작. `js/common.js`에 `openModal`(단순한 모달 3곳 이전). escapeHtml 은 이미 `core.js`(1-3), 목록 헬퍼는 `common.js`(3-1). 미룬 것: 전역 변수·typeof 교차참조 정리(번들러 없이 네임스페이스화 — 사실상 비목표), 다시 그리기가 있는 모달 3곳. 테스트 `tests/test_page_lifecycle.py`(화면 코드의 document/window 리스너는 이름 붙은 함수 + 떼는 짝).

**현재**
- 최상위 전역 변수가 375개이고, `typeof` 교차참조가 16곳이다.
- `escapeHtml`은 `inventory-probe.js`에 정의돼 있는데 다른 파일들이 쓴다.
- 공용 목록 헬퍼가 도달 불가능한 `collection.js`에 들어 있다.
- 브리지가 준비되기 전에 폴러가 시작된다(`analysis-progress.js:119-124`, `workspace.js:274-279`).
- 탭을 떠나도 인터벌이 남고(`logs.js:141-147`), 리스너가 렌더할 때마다 누적된다(`inventory.js:222`).

**변경**
- `js/common.js`를 만들어 이스케이프·모달·목록 헬퍼를 모은다.
- 페이지에 mount/unmount 수명주기를 도입한다.
- 번들링은 하지 않는다(OPTIMIZATION_PLAN 비목표).

### 3-8. 로깅·시각 표기 · S~M

**결과** ✅ — logging → 앱 로그 다리(EXE 에서 사라지던 storage_service 기록; 저장 연산 추적은 DEBUG 로 파일에만), 로그 보존(종류별 최근 20개 + 30일), `core/version.py`(EXE run 기록의 'unknown' 수정), 시각 헬퍼를 `core.utils.format_iso` 하나로, 실시간 경고에 날짜 포함 ISO(`at`·`resolved_at`) — 복원 때 경고마다 그 날짜를 붙인다. `structured_log.log_event` → `write_event`(이름 충돌). 표시용 인라인 strftime 48곳은 근거 없이 통일하지 않았다. 테스트 `tests/test_logging_and_time.py`.

**현재**
- `storage_service`에는 stderr 핸들러만 있다(`core/storage_service.py:37-45`). EXE는 `console=False`라 이 로그가 사라진다.
- `structured_log`에 같은 이름의 `log_event`가 있고, 이벤트마다 파일을 연다.
- 로그 보존 정책이 없다.
- 인라인 strftime/isoformat이 약 70건이다. `_now_iso`·`_app_version`이 여러 곳에 중복 정의돼 있다.

**원칙**
- 저장 데이터는 날짜를 포함한 ISO 형식을 필수로 한다.
- 표시용 포맷은 근거가 있을 때만 통일한다(OPTIMIZATION_PLAN 3-6의 결론을 존중).

---

## 4단계 — 기능 추가 (v0.9.x, 우선순위순)

| # | 기능 | 공수 |
|---|---|---|
| 4-1 | **자격증명 금고** | M |
| 4-2 | **클라우드 AI 전송 미리보기·승인** | S~M |
| 4-3 | **무인 정기점검** | M~L |
| 4-4 | **알림 채널** | M |
| 4-5 | **멀티벤더(Cisco)** | L |
| 4-6 | **SSH 호스트키 관리** | M |
| 4-7 | **회차 비교** | M |
| 4-8 | **실시간 감시 확장** | M |
| 4-9 | **작업 센터** | M |
| 4-10 | **운영 편의** | 각 S~M |

### 4-1. 자격증명 금고 · M ✅

**결과** ✅ — `core/secret_vault.py`가 Windows DPAPI(현재 사용자 범위, ctypes — 새 의존성 없음)로 봉인해 원래 자리에 `vault:dpapi:v1:…`로 둔다. 장비 목록(비밀번호·붙여넣은 개인키·passphrase·기본 비밀번호)은 `save_inventory`가 봉인하고 `load_inventory`가 풀어 메모리는 평문이라 접속하는 쪽은 그대로다. AI API 키는 `_save_ai_config`/`_load_ai_config`. 화면에는 `has_*`만 보내고(`public_device`), 화면이 보낸 목록은 장비 id(새로 추가한 행은 화면이 붙임, 없으면 이름에서 만든 id)로 기존 비밀값을 잇는다 — 새로 입력한 칸만 바뀌고 지우기는 `clear_secrets`. 연결 확인도 저장된 비밀값으로 한다. 엑셀 내보내기는 비밀번호를 기본으로 빼고('비밀번호 포함'을 고를 때만 평문, 개인키는 어느 쪽이든 안 넣음), 프로젝트 zip 은 기본으로 비우고 '포함'이면 풀어서 담으며 불러오면 바로 봉인한다. 예전 평문은 앱 시작 때 업그레이드 1이 한 번 봉인한다(장비 목록·레거시 ip_allocation·예전 credential.json·ai_config — 사용자가 내보낸 엑셀·CSV 와 앱 폴더의 레거시 원본은 건드리지 않음). 아무도 읽지 않던 평문 사본 `credential.json`은 더 만들지 않는다. 다른 PC·사용자에서 봉인된 값은 풀리지 않는다 — 접속 쪽은 '비밀번호 없음', 화면은 '이 PC에서 풀 수 없음 — 다시 입력'으로 안내하고, 다시 입력할 때까지 원래 값을 지우지 않는다. 1-3 에서 미룬 '비밀번호를 DOM 에 넣지 않기'도 함께 해소. 테스트 `tests/test_secret_vault.py`(18)·`tests/test_inventory_secrets_js.py`(2). 미룬 것: 키 파일 선택(`read_key_file`)은 사용자가 고른 파일의 본문을 화면 입력칸으로 한 번 보낸다(저장된 값은 보내지 않음), 실제 화면·EXE 에서의 확인.

- **내용**
  - 인벤토리 비밀번호·키·passphrase, credential.json, API 키를 Windows DPAPI(또는 keyring)로 암호화한다.
  - JS에는 `has_password` 플래그만 보낸다.
  - 엑셀·zip 내보내기에서는 기본으로 제외한다.
  - 기존 평문은 1회 이전한다.
- **연결 지점**: `engine/device_inventory_core.py:20-23` ✔, `api/inventory_api.py:6-31`, `engine/profile_manager.py:428-445`, `api/cloud_ai_api.py:164`

### 4-2. 클라우드 AI 전송 미리보기·승인 · S~M ✅

**결과** ✅ — 이 PC 밖으로 나가는 AI 분석(클라우드, 다른 PC 의 로컬 AI 서버)은 두 단계가 됐다(결정 D3). `preview_ai_transmission`이 보낼 내용(파일마다 `extract_suspicious_context` → `mask_for_external_ai` → 배치 분할 → 프롬프트)을 `ai_analysis/transmission.build_plan` 하나로 만들어 서버 메모리에 미리보기 id 로 맡기고(10분, 모드마다 하나, 한 번만 쓴다), 승인 창은 받는 곳(제공자·호스트·모델)·키 순서·항상 가리는 항목·파일별 요청 수와 글자 수·마스킹 뒤에도 남은 비밀값 의심을 보여 주며 '내용 보기'로 실제 프롬프트 전체를 받아 본다(`get_ai_transmission_part`, textContent). `start_ai_log_analysis(mode, preview_id)`는 승인한 미리보기의 내용만 보낸다 — 미리보기 뒤 AI 설정(항목·모델·주소)이나 활성 회차가 바뀌었으면 거부하고, 로그가 바뀌어도 승인한 내용만 나간다. 이 PC 안의 로컬 AI 는 승인 없이 바로 시작한다. `raw_log_analyzer`는 만들기(`build_prompts` → `PreparedRequests`)와 보내기(`send_prompts`)를 나눴고, 가리지 않고 만든 내용을 밖으로 보내려 하면 거부한다. 승인과 결과는 데이터 폴더의 `ai_transmissions.jsonl`(정리하지 않음)에 한 줄씩 남는다 — 시각·Windows 사용자·고객사/프로파일/회차·받는 곳·키 이름·파일·요청 수·글자 수·내용의 SHA-256, 결과는 보낸 요청 수·실패 파트·쓴 키(내용과 키 값은 남기지 않음). 키 로테이션(`KeyRing`): 사용 체크된 첫 항목과 같은 곳(제공자·주소)으로 가는 항목만 돌려 쓴다(다른 제공자로는 넘어가지 않음). 401/403 이면 그 키를 이번 분석에서 빼고, 429·500·502·503·504·529 면 다음 키로, 모두 막히면 22초 쉬고 최대 3바퀴. 모든 키가 거부되면 남은 파트는 보내지 않는다. 연결 테스트는 2xx 만 성공이다(401·402·403·404·429·5xx 는 실패 문구, GET 을 받지 않는 주소의 400/405 는 '키 미확인' 경고). Claude: 기본 모델을 제공자 표(`core/cloud_providers.py`)의 첫 항목 `claude-opus-5-5`로 옮겼다 — 1-2 의 예시는 Sonnet 5.5 였지만 현행 기본값인 Opus 5.5 로 두었고, 바꾸려면 표의 첫 항목이나 설정 화면에서 항목의 모델을 고른다. 목록은 Opus 5.5·Sonnet 5.5·Haiku 5.5·Fable 5.1 뒤에 예전 모델을 남겼고 저장된 선택은 바꾸지 않는다. 현행 모델은 thinking 이 늘 켜져 생각 토큰도 max_tokens 에 들어가므로 기본값·하한을 16000 으로 했다(예전 저장값 1500 은 하한으로 올려 보내고 화면에도 그렇게 보인다). 요청 제한 시간은 600초, 응답은 `type: text` 블록만 모은다. `anthropic-version`은 `2026-10-21`로 옮겼다 — 이 판의 변경(sampling 파라미터 거부 등)은 이 앱의 요청에 해당이 없다. 안전 분류기 거절(`stop_reason: refusal`)은 결과에 '[AI 분석 거절 - 파트 N]'으로 남기고(같은 내용이라 다시 보내지 않음), 출력 한도에 닿으면 결과 끝에 알린다. Opus 5.5·Opus 5·Sonnet 5.5·Fable 5.1 요청에는 서버 쪽 대체 모델(`fallbacks: "default"`, 베타 `server-side-fallback-2026-07-01`)을 붙인다 — 거절되면 같은 Anthropic API 안에서 다른 Claude 모델이 처리하고, 계정이 베타를 받지 않아 400 이면 그 기능 없이 한 번 더 보낸다. 외부로 나가는 내용에서는 활성 프로파일 장비 목록의 장비명도 모두 가린다 — 설명·LLDP·BGP 설명에 이웃으로 나오는 이름은 그 로그의 장비명이 아니라서 예전에는 그대로 나갔다(`transmission.neighbor_names`: 한 글자·숫자뿐인 이름은 낱말 단위로 가리면 분석을 망쳐 뺀다. `mask_for_external_ai`는 이름이 수백 개여도 긴 이름부터 묶은 패턴 하나로 한 번에 훑는다 — 장비 300대·44KB 에 약 50ms. 장비 목록을 못 읽거나 가릴 이름이 없으면 승인 창에 경고). 테스트 `tests/test_ai_transmission.py`(29). 미룬 것: 장비 목록에 없는 이름(통신사·다른 고객사 장비)은 가리지 못한다 — 승인 창의 '내용 보기'로 확인, effort 는 모델 기본값(Opus 5.5 는 medium), 스트리밍 없이 응답 전체를 기다린다(분석 도중 취소는 4-9), 실제 키·실제 화면·EXE 로는 확인하지 않았다(승인 창은 앱 스타일시트로 띄워 배치·버튼 동작만 확인).

- **내용**
  - 마스킹된 페이로드를 확인한 뒤 전송하고, 승인 이력을 남긴다.
  - 키 로테이션을 지원한다(지금은 첫 키만 쓴다).
  - 연결 테스트가 404/5xx를 성공으로 처리하는 문제를 고친다.
  - (1-2에서 이관) 기본 모델 id `claude-sonnet-4-6`(`ai_analysis/raw_log_analyzer.py`)을 설정값으로 빼고 현행 모델로 옮긴다. 현행 모델은 thinking이 항상 켜지므로 `max_tokens`를 다시 잡는다. `anthropic-version` 헤더 이전도 함께 검토한다.
- **연결 지점**: `api/log_analysis_run_api.py`(`start_ai_log_analysis`), `api/cloud_ai_api.py:91-95`

### 4-3. 무인 정기점검 · M~L ✅

**결과** ✅ — 워크스페이스의 '무인 정기점검' 카드에서 프로파일마다 일정 하나(매일 / 매주 요일 / 매월 1~28일 + 시:분, 보고서 형식 Excel·PDF)를 저장하면 `<사용자 데이터>/schedules.yaml`(project id 로 가리킴 — 이름이 바뀌어도 그대로)에 남고 Windows 작업 스케줄러에 `\AutoCheck\scheduled-<일정 id>`로 등록된다(`engine/task_scheduler.py` — schtasks 에 UTF-16 작업 정의 XML: 놓친 일정은 켜진 뒤 실행 `StartWhenAvailable`, 겹치면 새 실행은 건너뜀 `IgnoreNew`, 4시간 제한, 실행 계정은 등록한 사용자·로그인했을 때만 `InteractiveToken` — 비밀번호를 작업에 저장하지 않고 DPAPI 로 봉인된 장비 비밀번호(4-1)를 그 사용자만 풀 수 있어서). 작업은 `AutoCheck.exe --scheduled <id> --data-root <폴더>`(소스 실행이면 pythonw 로 main.py)를 부르고, main 은 창 없이 `engine/scheduled_inspection.run_scheduled_inspection` 을 한 번 돌린 뒤 종료 코드(0 성공·2 일부 실패·1 실패·3 건너뜀)로 끝난다. 실행은 수집(`engine/collector.collect_all`, execution_mode `scheduled`, 세션 점검과 같은 파일명) → 규칙 기반 로그 분석 → 보고서(이번 회차의 raw 만 — `build_context(log_dirs=)`: 모든 회차를 섞으면 수집하지 못한 장비가 지난 회차 로그로 '정상' 보고된다)이고, 결과(성공·일부 실패·실패·건너뜀, 수집 실패 장비와 이유, 이상 징후 수, 보고서 파일)를 `scheduled_runs.jsonl`·앱 로그에 남긴다. 화면은 카드의 실행 이력과 사이드바 '워크스페이스' 빨간 배지(확인하지 않은 실패, 모든 프로파일)로 알리고, '지금 실행'은 같은 실행을 앱 안에서 돌려 JobRunner 로 진행을 보인다. 3-1 결함 목록 대응: 경로는 AppPaths(데이터 폴더를 인자로 넘김), 받을 수 없는 일정(cron 식·없는 시각·29~31일)은 이유를 붙여 거절, 놓친 일정·겹침은 작업 스케줄러 설정으로, 실패는 이력·배지·로그로. GUI 활성 프로젝트 보호: 일정의 project id 로 직접 찾고 `set_active_project`를 부르지 않으며, 프로파일 잠금(`core/process_lock` — OS 파일 잠금, 프로세스가 죽으면 풀림)으로 같은 프로파일의 동시 실행을 막는다. 잠금은 앱을 열 때의 '중단된 run 정리'가 다른 프로세스의 run 을 중단으로 바꾸지 않게, 보관이 실행 중인 프로파일 폴더를 옮기지 않게도 한다. 보관(삭제)하면 일정과 작업을 함께 지운다. 레거시 이전·업그레이드가 남았으면 무인 점검은 하지 않고 '앱을 한 번 열라'는 실패로 남긴다(앱과 동시에 고치지 않게). EXE 는 `AutoCheck.spec`가 이미 api/engine/core 하위 모듈을 모두 담아 바꿀 것이 없다. 작업 정의 XML 은 작업 스케줄러의 검사 전용 등록(TASK_VALIDATE_ONLY — 아무것도 등록하지 않음)으로 매일·매주·매월 모두 통과를 확인했다. 사용자 승인을 받아 실제 작업 스케줄러에서도 시험했다(2026-10-28, 소스 실행): 앱 코드(`task_scheduler.register`)로 임시 데이터 폴더·없는 일정 id 의 작업을 등록하자 저장된 설정이 의도대로였고(Interactive 로그온·StartWhenAvailable·IgnoreNew·PT4H·다음 실행 시각), `schtasks /Run` 으로 실행하자 작업 스케줄러가 pythonw 로 main.py 를 띄워 3초 안에 임시 폴더의 `scheduled_runs.jsonl`·앱 로그에 '실패 — 일정이 없습니다'를 남겼고 '마지막 실행 결과'는 종료 코드 1 이었다. `unregister` 로 지우고 시험 전에 없던 `\AutoCheck` 폴더도 지워 원래대로 돌렸다(장비 접속·실제 데이터 폴더 접근 없음). 테스트 `tests/test_scheduled_inspection.py`(44 — 가짜 netmiko 로 수집→분석→보고서 전 과정, 잠금·보관·복구, 일정 검증·다음 실행, XML·schtasks 인자(실제 schtasks 호출은 막아 둠), 화면 API·배지, `main.py --scheduled` 하위 프로세스, 카드 이스케이프). 미룬 것: 정해진 시각에 저절로 깨어나는 실행·EXE(`AutoCheck.exe --scheduled`)·실제 장비로의 확인, 알림 채널(→4-4), 다른 프로세스가 돌린 무인 점검 뒤 실시간 감시 Baseline 갱신(앱이 감시를 다시 시작할 때 최신 점검 로그로 잡힌다), 장비 수백 대일 때의 소요 시간.

- **내용**
  - 앱 안에서 프로파일별 일정을 관리한다.
  - `AutoCheck.exe --scheduled <profile>`을 Windows 작업 스케줄러에 등록한다(지금 EXE 진입점은 main.py뿐이다).
  - 실행 이력과 실패 배지를 보여준다.
- **연결 지점**: `main.py`, `AutoCheck.spec`, `engine/collector.py`(3-1 에서 남긴 수집 체인). 옛 `engine/scheduler.py`는 3-1 에서 퇴역했다 — 3-1 의 스케줄러 결함 목록(CWD 상대 경로, `*/5`·`1-5` 무시, 실행 중 도래한 job 누락, 실패 통보 print 뿐)과 GUI 활성 프로젝트 보호를 새로 쓸 때의 요구사항으로 삼는다. 진행 상황은 `core/progress_engine` 대신 JobRunner(3-4)로 보고한다.

### 4-4. 알림 채널 · M ✅

**결과** ✅ — 환경 설정의 '알림 채널' 카드에서 Teams(Workflows·예전 커넥터가 모두 받는 Adaptive Card 봉투)·Slack(incoming webhook)·일반 웹훅(사내 메신저 — source·kind·severity·title·text 등을 담은 JSON)·메일(SMTP — STARTTLS·SSL·암호화 없음, 비밀번호는 암호화된 연결에서만) 채널을 여러 개 등록하고 '시험 보내기'로 확인한다(`engine/notifications.py`, `api/notification_api.py`, `web_ui/js/settings-notifications.js`). 설정은 `<사용자 데이터>/config/alarm.yaml` — 웹훅 주소(경로가 곧 토큰이다)와 SMTP 비밀번호는 DPAPI 로 봉인하고(`SECRET_FILE_NAMES`에 alarm.yaml, `SECRET_KEYS`에 webhook_url) 화면에는 저장 여부·웹훅 호스트만 보낸다(빈 칸은 그대로, '비밀번호 지우기'로 지움). 웹훅은 https 만 받는다(같은 PC 의 http://localhost 만 예외). 보내는 사건: 실시간 감시 경고(기본 CRITICAL 만, MAJOR 이상으로 넓힐 수 있음 — `RealtimeWatchService.push_alerts`에 걸었고 감시 스레드를 막지 않게 백그라운드로 보낸다. 감시 시작 전 기록·숨긴 경고·세션 점검 직후 자동 분석 결과는 보내지 않는다), 무인 정기점검 실패(사전점검·전체 수집·보고서 실패), 무인 점검 수집 실패(일부 장비) — 무인 점검은 그 실행 안에서 바로 보낸다(작업 스케줄러가 띄운 프로세스는 곧 끝난다). 임계값·가림·중복 억제는 alarm.yaml 의 옵션이다: 가림 수준(기본 비밀값·IP·MAC·계정 — 장비명은 남긴다, '장비명까지'·'가리지 않음'), 같은 사건은 N분(기본 10)에 한 번, 채널마다 시간당 최대 M건(기본 30 — 넘친 건수는 다음 메시지에 적는다), 사건별 켜고 끄기. 실패는 앱 로그와 채널의 '마지막 전송' 상태에 남고 부른 쪽으로 던지지 않는다. 웹훅 주소는 오류 문구·로그에 호스트만 남기고, 로그 필터(`core/secret_redaction`)도 Slack·Teams 웹훅 토큰(`hooks.slack.com/services/…`, `…webhookb2/…`, `sig=`)을 지운다. 앱을 닫을 때 밀린 알림을 최대 5초 기다린다. 4-2 의 키 순환 규칙에 맞춰 클라우드 API 키 카드 설명도 고쳤다(같은 제공자 안에서만 넘어간다). 4-1 테스트 하나가 봉인값에 우연히 섞인 'dp' 에 걸려 가끔 실패하던 것도 고쳤다. 카드는 앱 스타일시트로 띄워 종류 전환·저장 값(빈 비밀 칸은 보내지 않음, 지우기)·1000px 폭 넘침을 확인했다. 테스트 `tests/test_notifications.py`(37). 미룬 것: 실제 Teams·Slack·메일 서버로의 전송 확인(설정 화면의 '시험 보내기'로 사용자가 확인), GUI 세션 점검의 전송 실패 알림(작업자가 화면 앞에 있다).

- **현재 문제**: 알람이 사실상 울리지 않는다.
  - FAIL은 최대 High로 매핑되는데, AlarmStep은 Critical만 보낸다(`core/finding.py:36-40`, `pipeline/steps.py:233`).
  - 핸들러는 console·file뿐이다.
  - (3-1 에서 `alarm/`·AlarmStep 은 채점 파이프라인과 함께 퇴역했다 — 알림 채널은 새로 만든다.)
- **내용**
  - Webhook(Teams·Slack·사내 메신저)과 SMTP 핸들러를 추가한다.
  - `config/alarm.yaml`로 임계값·마스킹·중복 억제를 설정한다.
  - 알림 대상: 실시간 CRITICAL, 무인 점검 실패, 수집 실패.
- **연결 지점**: 실시간 감시(`engine/realtime_watch_service.py`의 경고 push), 무인 점검(4-3)

### 4-5. 멀티벤더(Cisco) · L ✅

**결과** ✅ — 결정 D4(IOS/IOS-XE 먼저, NX-OS 다음)대로 셋 다 넣었고, 세 커밋으로 나눴다.
- **4-5a 벤더별 접속·enable 비밀번호**: 장비 목록의 '벤더' 열(Arista EOS·Cisco IOS·Cisco IOS-XE·Cisco NX-OS — 예전에 적어 둔 다른 글자는 지우지 않고 어떻게 읽었는지 함께 보인다)을 `core/vendors.platform_of`가 플랫폼으로 읽어 netmiko device_type 을 고른다. 비어 있으면 예전처럼 Arista, 모르는 벤더(Juniper, Cisco ASA·IOS-XR 등)는 접속하지 않고 '지원하지 않는 벤더'로 알린다(세션 점검도 그 세션에는 명령을 보내지 않는다 — 터미널로는 쓸 수 있다). enable 비밀번호(장비별 `enable_secret`·기본 `default_enable_secret`)는 4-1 규칙대로 봉인하고 화면에는 has_* 만, 로그인 비밀번호로 대신하지 않는다. 수집기는 enable 실패를 더는 삼키지 않는다 — 재시도 없이 그 장비를 실패로 남기고(재시도마다 장비에 인증 실패가 쌓인다; 세션 끊김은 예전처럼 재시도), Cisco 는 enable 비밀번호가 없으면 시도하지 않는다. 세션 점검은 enable 을 통짜 명령에서 빼서 먼저 보내고 프롬프트나 'Password:'가 올 때까지 기다려(TACACS 가 느려도) 비밀번호로 답한다 — 예전엔 장비가 비밀번호를 물으면 다음 명령 줄이 비밀번호로 들어갔다. 실패하면 그 장비는 점검하지 않는다(사용자 모드의 권한 오류 로그를 결과로 남기지 않는다).
- **4-5b 체크ID 로 벤더별 명령**: 카탈로그가 버리던 '체크ID'를 `check_id`로 지킨다 — 체크ID 가 생기기 전의 카탈로그는 읽을 때 기본 카탈로그와 명령 글자로 잇고, 카탈로그 엑셀 내보내기·가져오기에 열을 더했다. `engine/vendor_commands.py`의 표(기본 카탈로그 48개 체크ID 전부)가 Cisco IOS·IOS-XE·NX-OS 의 같은 목적 명령을 고른다(예: show port-channel dense → show etherchannel summary / show port-channel summary, show mlag → NX-OS show vpc, VRRP → HSRP 도). 같은 목적의 명령이 없으면(VARP·큐 카운터 등) 보내지 않고 세션 점검 시작 로그에 적는다. Arista 는 예전 명령 그대로, 체크ID 없는 사용자 명령은 모든 장비에 그대로. 세션 점검·무인 점검 모두 장비마다 고른다. 기본 카탈로그 엑셀을 openpyxl 로 읽게 바꿔 앱 코드에서 pandas 를 쓰는 곳이 없어졌다.
- **4-5c Cisco 출력 해석**: 보고서 판정은 양식의 EOS 명령 패턴 뒤에 같은 내용을 담은 Cisco 명령을 이어서 찾는다(`report_patterns` — 사용자 PC 의 예전 양식 사본은 commands 가 번들 값을 이기므로 양식이 아니라 코드에서 잇는다). 그 플랫폼에 없는 기능(IOS·IOS-XE 의 MLAG, IOS 의 EVPN·VXLAN)은 '해당없음'. 평가기에 Cisco 형식(Uptime 'uptime is', CPU 5분 평균·NX-OS idle, Free Memory 'Processor Pool'·NX-OS 'Memory usage', Interface Error 의 Cisco 포트 이름, Route Table 의 IOS 표·NX-OS VRF 합계), 장비 정보(OS 버전·S/N·모델)에 IOS·IOS-XE·NX-OS 규칙. 판정 플랫폼은 show version 으로 알아보고 없으면 벤더 칸. 구성도는 CDP·LLDP 상세(표는 한 이웃이 두 줄로 쪼개져 읽지 않는다)·EtherChannel/port-channel summary·NX-OS vPC 를 읽고, 포트 이름 정규화(Fa·Tw·Fi·Twe·Hu …)와 Cisco 포트 상태(err-disabled, NX-OS 의 잘린 표기)를 더했다. 이름 대신 MAC·IP 로 알리는 이웃이 '0050' 노드가 되던 것(EOS LLDP 에도 있던 문제), PDF 서버 판별의 `hpe?`(→ `\bhpe?\b`)도 고쳤다.
- 장비 목록 화면은 앱 스타일시트로 띄워 벤더 선택(목록 밖 값·이스케이프)·enable 비밀번호 입력/지우기·기본 enable 비밀번호 저장 값을 확인했다. 열이 둘 늘어 1280px 창에서는 표가 가로로 더 스크롤된다(예전에도 사이드바를 펴면 스크롤됐다).
- 테스트 `tests/test_multivendor.py`(82) + 무인 점검 1. Cisco 출력은 Cisco 명령 레퍼런스의 예시 형식으로 만든 표본이다.
- **확인하지 않은 것·미룬 것**: 실제 Cisco 장비 접속과 출력(명령 표·파서·평가기는 레퍼런스 형식 기준 — 표본을 받으면 맞춘다), 규칙 판정(`config/log_rules.json`)의 Cisco 문구(keyword_ok 항목은 EOS 문구 규칙으로 본다), 실시간 패턴의 데이터화(`config/realtime_patterns.json` — Cisco syslog 일부는 2-11 에서 이미 인식), 프롬프트 정의 통합(실기 코퍼스 필요 — 그대로 남김), 실시간 상태 조회(`engine/state_poller.py`)의 Cisco 명령, StackWise·VSS, NTP 서버 추출·ACL 헤더(보고서가 쓰지 않는 파서), HSRP 와 VRRP 를 함께 쓰는 IOS 장비는 HSRP 출력만 판정, pandas 를 requirements 에서 빼는 것(빌드 확인과 함께).

- **내용**(세부 목록은 부록 A)
  - 인벤토리의 vendor 값으로 netmiko device_type을 고르고, enable secret을 지원한다.
  - 카탈로그의 '체크ID' 열로 벤더별 명령을 생성한다. 지금은 `engine/command_catalog.py:36-54`가 이 열을 버린다.
  - Cisco 파서를 추가한다.
  - 실시간 패턴을 `config/realtime_patterns.json`으로 데이터화한다.
  - 구성도에 CDP·vPC를 지원한다.
  - 보고서 정규식을 벤더별로 나눈다.
  - 프롬프트 정의를 하나로 합친다(2-10·3단계에서 넘어옴): `engine/baseline_diff_engine.py`·`engine/log_rule_engine.py`·`engine/stream_device_matcher.py`·`engine/inspection_evaluator.py`가 첫 글자 숫자 허용·`#` 앞 공백·길이를 서로 다르게 본다. 판정 동작이 바뀌므로 실기 로그 코퍼스로 고른다.
- **연결 지점**: `engine/collector.py:96` ✔, `parsers/`(벤더 드라이버 `plugins/vendors/`는 3-1 에서 퇴역)

### 4-6. SSH 호스트키 관리 · M ✅

**결과** ✅ — 예전엔 paramiko(터미널·연결 확인·실시간 상태 조회)와 netmiko(SSH 수집·무인 점검) 모두 `AutoAddPolicy`라 장비 자리에 끼어든 다른 기계에도 그대로 로그인해 비밀번호를 넘겼다. `engine/host_keys.py`가 TOFU 를 맡는다: 처음 접속한 장비(주소:포트)의 키를 기억하고(OpenSSH 의 accept-new 와 같다), 기억한 키와 다르면 **비밀번호를 보내기 전에** 끊고 `HostKeyChanged`를 낸다. 받은 키는 '바뀐 키'로 남고 기억한 키는 그대로다 — 장비 목록의 'SSH 호스트키'(접이식, 바뀐 키가 있으면 펼쳐지고 요약이 빨갛게 보인다)나 그 장비 행의 연결 결과('호스트키 바뀜' + '다시 신뢰')에서 기억한 키·지금 키의 지문(SHA256)을 확인한 뒤 '다시 신뢰'할 때만 바뀐다. '잊기'는 기억한 키를 지워 다음 접속이 새로 기억하게 한다. 키는 **고객사마다** 따로 기억한다 — 여러 고객사를 도는 점검자는 같은 사설 IP 를 고객사마다 다른 장비로 만나는데, 한 목록에 두면 고객사를 옮길 때마다 '바뀜' 경고가 나고 경고에 익숙해지면 진짜 가로채기를 놓친다(고객사가 없는 예전 프로파일은 그 프로파일 단위). paramiko 는 기억한 키를 클라이언트에 넣어(그 종류의 키를 먼저 협상한다) 다르면 인증 전에 `BadHostKeyException`, 처음이면 정책이 기억한다. netmiko 는 기억한 키 한 줄을 임시 known_hosts 로 넘기고(`alt_host_keys`/`alt_key_file` — 접속 뒤 지운다) 처음 보는 장비는 접속한 뒤 받은 키를 기억한다. netmiko 가 감싸서 올리는 `BadHostKeyException`도 예외 사슬에서 찾아 같은 오류로 바꾸고, 수집은 재시도하지 않는다. 연결 확인은 키를 처음 기억했을 때 결과에 지문을 적는다. 저장은 `<사용자 데이터>/ssh_host_keys.json`(공개키라 봉인하지 않는다) — 화면과 무인 점검 프로세스가 함께 쓰므로 읽고-고치고-쓰는 동안 프로세스 사이 잠금을 잡는다. 테스트 `tests/test_host_keys.py`(17 — 127.0.0.1 에 띄운 진짜 SSH 서버(paramiko 서버 모드)의 호스트키를 바꿔 끼워, 터미널 경로와 진짜 netmiko 수집 경로 모두 비밀번호가 서버에 가지 않고 재시도하지 않음을 확인). 화면은 앱 스타일시트로 띄워 펼침·요약·'다시 신뢰'/'잊기' 호출을 확인했다. 미룬 것: 실제 장비로의 확인, 호스트키 변경 알림 채널 이벤트(지금은 무인 점검의 '수집 실패' 알림에 사유로 실린다), 장비에서 지문을 읽어 와 대조하는 기능.

- **내용**: known_hosts와 TOFU(최초 접속 시 지문 기록, 바뀌면 경고), 재신뢰 버튼
- **연결 지점**: `engine/ssh_client.py:96`, `collector.py`

### 4-7. 회차 비교 · M ✅

**결과** ✅ — 계획의 `compare_runs`·`generate_diff`는 3-2 에서 지웠으므로(쓰는 곳이 없었다) 새로 만들었고, 세 커밋으로 나눴다.
- **4-7a 회차 비교**(`engine/round_compare.py`, `api/round_compare_api.py`, `web_ui/js/workspace-compare.js`): 같은 고객사의 두 점검(회차 = 장비별 최신 로그, 또는 회차 안의 Run)을 원본 로그에서 다시 판정해 비교한다 — 보고서 판정(`build_context`)과 구성도(`build_topology`)를 그대로 써서 보고서·구성도 탭과 같은 판정끼리 견주므로, 보고서를 만들지 않은 회차와도 비교된다(보고서의 '전월 값'은 보고서를 만든 회차의 스냅샷뿐이다). 점검 항목은 나빠짐/좋아짐/값 바뀜 — 실측값(CPU·메모리)은 상태가 바뀔 때만, 가동 시간은 '기준 값 + 두 점검 사이 시간'보다 짧을 때만 '재부팅'(EOS·IOS·NX-OS·Linux 표기). 장비 정보는 OS 버전·S/N·모델, 구성도는 물리 링크의 새로 생김/사라짐/상태 바뀜 — 양 끝 모두 그 점검에서 이웃 정보를 내지 못했으면 '확인 불가'로 따로 둔다(로그가 없는 장비의 링크를 '사라짐'으로 알리면 단선 경보가 쏟아진다). 워크스페이스의 '회차 비교' 카드(기본: 이 회차와 바로 앞 회차)와 Run 이력 행마다 '비교'(그 Run 과 바로 앞 Run, 없으면 앞 회차). 화면이 보낸 회차·Run 이름은 이 고객사의 점검 목록에 있는 것만 받는다.
- **4-7b 구성도 비교**: 구성도 탭의 '지난 회차와 비교' — 새 링크는 상태 색을 그대로 두고 파란 후광, 지난 회차에만 있던 링크는 빨간 점선과 '사라짐'으로 더 그리고 상태 줄에 요약(새/사라짐/확인 불가)을 보인다. 배치를 정한 뒤에 얹어서 비교를 켜고 꺼도 장비 자리는 그대로다. 지난 회차의 링크는 그 회차 로그 파일이 그대로면 다시 만들지 않는다(구성도 탭은 2초마다 부른다). 'SVG 저장'도 비교를 얹을 수 있다 — 그러면서 SVG 저장이 캐시된 구성도에 실시간 상태를 바로 쓰던 것을 복사본에 쓰게 고쳤다.
- **4-7c PDF 부록**: 정기점검 PDF 끝에 '부록. 네트워크 구성도' — 그 보고서의 로그로 만든 구성도(구성도 탭과 같은 배치·곡선, 사용자가 끌어 옮긴 배치 그대로)와 앞 회차 대비 링크 변화 표. SVG 를 PDF 에 넣을 라이브러리(svglib·cairo)를 새로 들이지 않고, `topology_svg.figure()`가 넘기는 값을 reportlab 으로 그린다(장비는 상자 + 종류 글자, DOWN ✕, 사라진 링크 점선·'사라짐', 새 링크 후광, 범례). 구성도를 만들지 못하면 부록 없이 보고서를 만든다.
- 테스트 `tests/test_round_compare.py`(20 — 두 회차·Run 의 EOS 로그로 기본 쌍·회차/Run 비교·재부팅·OS 업그레이드·링크 3종·확인 불가·잘못된 이름, 구성도 표시·SVG·API 캐시·저장본, PDF 부록·실제 PDF·실패해도 보고서). 회차 비교 카드는 앱 스타일시트로 띄워 확인했고, PDF 부록 그림은 reportlab 의 SVG 출력으로 뽑아 확인했다.
- 미룬 것: 실제 장비 로그로의 확인, 비교 결과를 보고서에 표로 넣는 것(지금은 링크 변화만 부록에), 세 회차 이상의 추세.

- **내용**
  - 이미 구현된 `compare_runs`·`generate_diff`를 Run 이력에 연결한다.
  - 구성도를 회차끼리 비교한다(신규·소실 링크).
  - 구성도 SVG를 PDF 부록으로 넣는다.
- **연결 지점**: `engine/inspection_report_builder.build_context()`(회차별 항목 값), `engine/topology_svg.py`(standalone 렌더 재사용). 예전 `LogManager.compare_runs`(parsed JSON 비교)는 쓰는 곳이 없어 3-2 에서 지웠다

### 4-8. 실시간 감시 확장 · M ✅

**결과** ✅ — 네 커밋으로 나눴다.
- **4-8a 열린 장애**(42445bc): 판정 엔진의 상태표(아직 복구를 보지 못한 링크 다운·라우팅 인접 끊김·MLAG 피어 끊김·설정 삭제)를 실시간 감시 상단의 '열린 장애' 줄로 보인다 — 장비·대상·경과 시간, 재시작 전부터·상태 폴링·무시됨 표시, 왼쪽 띠는 매달린 경고의 심각도, '해제'. 엔진은 감시를 시작할 때마다 새로 만들어지므로 모니터가 사본을 들고 프로파일 저장본에 남기고, 감시를 다시 시작하면 새 엔진의 상태표를 다시 세운다(원래 시작 시각 유지) — 예전엔 재시작 전에 열린 장애를 닫을 대상이 없어서 장비가 돌아와도 되살린 경고가 영영 '미해결'이었다. 경고 id 에 실행 표식을 붙였다(번호가 1부터 다시 나가 되살린 경고와 새 경고의 id 가 겹치면 '해결'·자동 해제가 엉뚱한 쪽에 걸렸다). '해결'은 그 경고가 매달린 장애를 닫고 같은 장애의 다른 경고도 함께 해결한다(자동 복구와 같은 뜻, 감시가 멈춰 있어도). '초기화'는 열린 장애도 비운다. 고침: 3-5 이후 구성도 탭이 옛 속성 이름(`_baseline_diff_engine` 등)을 읽어, 실시간 감시의 링크 다운·관측·장비별 미해결 배지가 한 번도 얹히지 않았다(테스트는 그 함수를 바꿔 끼워서 못 잡았다).
- **4-8b 감시 건강**(72b1188): 감시기가 파일별 마지막 성장 시각(처음 본 파일은 수정 시각)을, 상태 폴러가 실패 종류(인증 실패·호스트키 바뀜·접속 실패)를, 모니터가 syslog·명령·폴링을 마지막으로 '지금' 받은 시각(되짚어 읽은 줄은 빼고, 저장본에 남는다)을 낸다. 서비스가 이것을 심각한 순서의 문제 목록으로 묶어 문제가 있을 때만 배너로 보인다 — 빨강(폴링 인증 실패·호스트키 바뀜, 사람이 고쳐야 한다), 주황(폴링 접속 실패·세션 로그 없음), 중립(세션 로그가 20분 넘게 늘지 않음 — 쉬는 세션일 수도 있어 단정하지 않는다). 툴바의 '감시 건강' 창은 장비별 마지막 기록·syslog·명령·폴링을 보인다. 폴링 델타에는 바뀔 때만 실린다(시각만 담고 '몇 분째'는 화면이 만든다).
- **4-8c 규칙 실험실**(0422480): 붙여넣은 줄을 실시간 감시와 같은 판정기에 한 줄씩 넣는다 — diff 엔진(명령/출력·모드, 경고·예정·복구), 규칙 엔진(맥락, 구조 억제, 서명·카운터·키워드, 키워드 억제 — 최종 판정은 `RuleEngine.evaluate` 그대로이고 테스트가 줄마다 대조한다), 실시간 목록에 올라가는지(아니면 major 미만·show logging 과거 기록·60초 중복·diff 엔진이 판정한 줄이라 버림). 판정기는 매번 새로 만들어 감시 중인 판정을 건드리지 않고, 장비를 고르면 감시의 Baseline 과 대조한다. 규칙 파일 린트 — 컴파일 안 되는 정규식, 빈 줄에도 맞는 정규식, 빈 정상 문구, 컴파일 안 되는 scope, 겹친 id, 모르는 심각도, 성립할 수 없는 상관 규칙, 이름 오타. 지금 규칙 파일은 깨끗하다(서명 33개 중 실시간에 올라가는 것 26). 해 보니 Cisco 식 카운터 표 머리글(`FCS-Err`)은 `counter_columns`(Arista 식 `FCS`·`Align`)에 맞지 않는다 — 4-5 에서 미룬 Cisco 규칙 문구와 같은 일이다.
- **4-8d 감사 이력 부록**(dd7ea54): 회차의 감시 저장본에서 경고마다 발생과 처리(자동 복구와 근거 줄·지속 시간, 수동 해결·무시와 메모, 미해결)를 뽑아 정기점검 PDF 의 '부록. 실시간 감시 이력'과 Excel 의 '실시간감시이력' 시트로 낸다 — 요약(등급·처리별 건수, 기록 기간, 감시 장비와 syslog 를 받은 장비 수)과 작성 시점에 열려 있던 장애 포함. 비밀값은 가리고, 장비에 남은 과거 기록과 보고서에 넣지 않은 장비의 것은 뺀다. 저장본은 읽기만 한다(깨졌으면 .bak). 보고서 탭·워크스페이스는 보고서를 만들기 전에 감시가 묶어 둔(10초) 변경을 저장한다.
- 테스트 1189 → 1234: `tests/test_realtime_open_conditions.py`(12)·`test_realtime_watch_health.py`(10)·`test_rule_lab.py`(13)·`test_realtime_audit.py`(9), 델타 테스트는 새 섹션(open_conditions·health)을 모형과 합친 상태 비교에 넣었다. 덤으로 0.2초 창 때문에 바쁠 때 흔들리던 저장 지연 테스트를 1초로. 화면(열린 장애 줄·감시 건강 배너와 창·규칙 실험실)은 앱 스타일시트로 띄워 확인했고, 부록은 실제 PDF·Excel 을 만들어 표 폭(170mm)과 시트 값을 확인했다 — PDF 를 그림으로 뽑을 도구가 없어 쪽 모양은 눈으로 보지 못했다.
- 미룬 것: 실제 SecureCRT 세션·장비로의 확인, 실험실에서 저장하기 전의 규칙 초안을 시험하는 것(지금은 저장된 규칙 파일만), '기록 멈춤' 기준(20분) 설정, Cisco 카운터 열 이름.

- **내용**
  - '열린 장애' 패널과 영속화: `StateTracker.open_conditions()`를 쓴다(지금 UI에서 쓰지 않음).
  - 감사 이력 내보내기: 보고서 부록으로 낸다.
  - 감시 건강 배너: syslog 마지막 수신, 파일 성장 정지, 폴링 인증 실패를 보여준다.
  - 규칙 실험실: 줄을 붙여넣으면 억제·서명·diff 분류 결과와 린트를 보여준다.
- **연결 지점**: `engine/realtime_monitor.py`, `engine/log_rule_engine.py`

### 4-9. 작업 센터 · M ✅

**결과** ✅(00983bd) — 상태바의 '작업' 버튼 하나와 위로 여는 창이 백그라운드 작업 일곱 가지(세션 점검, 무인 정기점검 '지금 실행', 규칙·로컬 AI·클라우드 AI 분석, 워크스페이스 보고서·내보내기)의 진행·취소·완료 이력을 함께 보인다(`api/job_center_api.py`·`web_ui/js/job-center.js`). 버튼은 도는 작업이 있으면 첫 작업의 이름·진행률(외 N)과 진행바, 방금 끝난 작업은 10초 동안 그 결과, 창을 열어 보지 않은 오류가 있으면 빨갛게 보인다. 창은 진행 중(진행바·경과/남은 시간·대기·메시지·'취소' — 두 번 눌러야 한다)과 이번 실행에서 끝난 작업(종류를 넘어 최신순 20개 — 완료/취소됨/오류, 시각, 걸린 시간, 결과 사본 대신 한 줄 요약)이다. 상태바에 따로 있던 점검·분석 진행바는 지웠다. 분석 취소를 이었다: 규칙 분석은 파일 사이, AI 분석은 파일·파트 사이와 재시도 대기(22초)·요청 사이 쉼(바로 끊는다)에서 본다 — 보내는 중인 HTTP 요청 하나는 끝까지 기다리고, 분석한 파일은 남고, 멈춘 파일의 남은 파트는 '[AI 분석 중단]'으로 적고, 승인한 외부 전송은 승인 이력에 'cancelled'(보낸 요청 수와 함께)로 남는다. 멈출 수 없는 것: 무인 점검 '지금 실행'(수집기에 멈춤 신호가 없다), 보고서·내보내기(짧고, 끝까지 쓴 파일을 '취소됨'으로 적으면 거짓이 된다). JobRunner 이력에 시작 시각과 `history(kind)`. 테스트 `tests/test_job_center.py`(11), 화면은 앱 스타일시트로 띄워 확인(진행·두 번 눌러 취소·Esc/바깥 클릭·방금 끝난 결과). 미룬 것: 무인 점검 '지금 실행'의 취소(수집기까지 멈춤 신호를 넘겨야 한다), 보내는 중인 AI 요청을 끊는 것, 작업 이력의 영속화(지금은 이번 실행 동안만 — 무인 점검은 따로 이력이 있다), 보고서 탭의 보고서 생성(바로 기다리는 호출이라 작업이 아니다).

- **내용**: 상태바에서 점검·분석·보고서·내보내기의 진행·취소·완료 이력을 보여준다. 3-4 에서 JobRunner 에 취소(`request_cancel`)·완료 이력(`history`)이 생겼다 — 분석 루프가 `cancel_requested()`를 보게 하고 화면을 잇는 일이 남았다.
- **연결 지점**: 3-4 이후

### 4-10. 운영 편의 · 각 S~M ✅

**결과** ✅ — 여덟 커밋으로 나눴다.
- **4-10a 보관함 화면**(fd5248b): 고객사/정기점검 창 머리의 '보관함' — 보관본 목록(최근 것 먼저), '복원'(같은 이름·id 의 프로파일이 있어 서버가 거절하면 그 이유를 그대로), 복원 정보가 없는 예전 보관본은 복원을 막고 폴더를 옮기라고 안내, '영구 삭제'는 정기점검 이름을 입력해야 지운다. 보관함 API 3개를 화면이 써서 `test_api_surface` 의 RESERVED 가 비었다.
- **4-10b 진단 번들**(524d14a): 전체 로그 탭의 '진단 번들' — about(버전·환경·패키지, Windows 사용자 이름은 `<user>`)·workspace(개수만)·checks(규칙 린트·필요 패키지·쓰기)·최근 앱 로그 3개(끝 2MB)·사용자 설정 사본·manifest 를 zip 하나로(`engine/diagnostics.py`). 비밀은 언제나 지운다(설정은 구조로 읽어 '[가림]'·'[봉인된 값]', 로그는 줄 단위). '이름·주소도 가리기'(기본 켬)는 장비·고객사·프로파일 이름과 IP·MAC·계정·도메인을 같은 글자 수의 '*'로 — 숫자와 구분자뿐인 이름은 로그의 날짜를 망가뜨려서 가리지 않는다. 점검 로그·보고서·장비/고객사 목록·외부 AI 전송 이력·호스트키는 담지 않는다.
- **4-10c 마스킹 미리보기·누출 검사**(275708c): 마스킹 탭의 '미리보기'(파일을 쓰지 않고 가릴 값의 수, 바뀌는 줄 전/후, 남는 것 — 비밀·골랐는데 놓친 값·고르지 않은 값·장비 이름)와 실행 뒤 요약의 '남은 것'(`log_masking.count_matches`·`leak_report`·`preview_masking`). 누출 검사가 버그를 찾았다 — 'Device Hostname'을 골라도 프롬프트(`Core1#`)의 장비명이 남았다. 선언을 마스킹한 뒤에 읽어서 Arista 의 '! device:' 주석이 있을 때만 우연히 가려졌다. 외부 AI 로 보내는 강제 마스킹도 같은 함수라 함께 고쳐졌다.
- **4-10d 로그 보기 쪽 나누기·찾기**(19fbea5): `read_log_file(path, start_line, max_lines)`(기본 5,000줄)·`search_log_file`(파일 전체, 정규식·대소문자, 찾은 줄 500개까지) — 수십 MB 로그가 화면을 멈추지 않는다. 로그 보기·분석 결과·마스킹 결과가 같은 막대(줄 범위·이전/다음·찾기·찾은 줄로 이동)를 쓴다.
- **4-10e 장비 별칭**(15c1d11): `core/device_names.DeviceNameIndex`(장비명·별칭 → 도메인을 뗀 짧은 이름, 두 장비가 겹치면 맞추지 않는다, 장비명이 다른 장비의 별칭보다 먼저, 주소는 줄이지 않는다)를 구성도의 LLDP 이웃·실시간 감시의 파일 판정·보고서의 장비 목록 조인이 함께 쓴다. 장비 목록에 '별칭' 칸(엑셀 열은 맨 끝).
- **4-10f 고객사·벤더별 보고서 템플릿**(c6ed644): `data/<고객사>/report_template.yaml` 을 앱 템플릿 위에 얹는다(`apply_customer_layer` — meta 는 적은 키만, 항목은 이름으로 고치거나 뒤에 더하고 `remove_items` 로 빼고, 번호는 다시). 항목의 `vendors` 에 없는 플랫폼의 장비는 '해당없음'. 보고서와 회차 비교가 같은 템플릿을 쓴다. 보고서 탭에 템플릿 상태 줄과 '고객사 템플릿 만들기'.
- **4-10g 작업공간 점검·자동 백업**(99a7427): 워크스페이스의 '작업공간 점검·백업' 카드. 무결성 점검(`engine/workspace_check.py` — 읽기만 한다)은 읽을 수 없는 프로젝트 정보·장비 목록, 목록에 없는 고객사 폴더·연결되지 않은 점검 데이터·데이터 폴더가 없는 프로파일(폴더 이름은 대소문자를 가리지 않고 견준다), session.json 이 깨진 Run, 읽을 수 없는 설정·상태 파일(.bak 이 있으면 그것으로), 이 PC 에서 풀 수 없는 봉인 비밀번호, 10분 넘게 남은 임시 파일과 앱이 옮겨 둔 .corrupt 파일을 할 일과 함께 보인다. 자동 백업(`engine/workspace_backup.py`)은 앱 설정·labs 의 프로파일 정의·고객사 템플릿·프로파일 메타·보고서 기록·감시 상태를 `backups/workspace_<시각>.zip`(manifest 포함, 봉인된 비밀번호는 봉인된 채로)으로 — 창이 뜬 뒤 하루 한 번, 최근 10개. 점검 로그·보고서 파일·앱 로그는 담지 않는다.
- **4-10h 골든 픽스처 도구**(7cb5b8b): `tools/promote_fixture.py` 가 실제 출력을 같은 모양의 가짜로 바꾼다(장비 이름 DEVICE-n, IPv4 는 198.18.0.0/15 의 /24 로 호스트 자리 그대로, IPv6 2001:db8::, MAC 02:00:00:…, 일련번호는 같은 길이, 계정·도메인·설명·VLAN 이름·SNMP 위치, 배너, 비밀). 같은 값은 늘 같은 가짜라 파서가 그대로 읽는다. 누출 검사(남은 원래 값 — 이름은 다른 낱말 속까지, 가짜가 아닌 주소·MAC·FQDN, 비밀)를 통과해야 `tests/fixtures/golden/<이름>/`(session.log·expected.json·fixture.json)를 쓴다. expected.json 은 플랫폼·명령·장비 정보·보고서 판정(번들 기본 양식)·구성도 사실(`topology_builder.device_facts` 로 꺼냈다)·로그 규칙 결과다. `refresh`·`check` 명령이 있고, 도구는 AUTOCHECK_HOME 을 임시 폴더로 돌려 실제 데이터 폴더를 건드리지 않는다.
- 테스트 1248 → 1300: `tests/test_diagnostics.py`(5)·`test_masking_preview.py`(7)·`test_log_view_paging.py`(4)·`test_device_names.py`(8)·`test_report_templates.py`(6)·`test_workspace_care.py`(10)·`test_promote_fixture.py`(7)·`test_golden_fixtures.py`(3). 화면은 보관함 창·마스킹 미리보기·로그 보기 막대·작업공간 카드를 앱 스타일시트로 띄워 확인했다(장비 목록의 별칭 칸과 보고서 탭의 템플릿 줄은 띄워 보지 않았다).
- 미룬 것: **실출력 골든 픽스처는 아직 0개다** — 저장소에 실제 장비 출력이 없다. 실장비(특히 Cisco) 출력을 승격해야 v1.0 완료 정의 5번(공백 영역의 실출력 골든 테스트)이 채워진다. 여러 장비를 한 픽스처로 승격하는 것(구성도 링크 짝까지), 백업 되살리기 화면(지금은 zip 에서 꺼낸다)과 무인 점검 프로세스의 자동 백업, 무결성 점검의 고침 버튼.

- 프로파일 보관함·복원 UI(2-5)
- 진단 번들 내보내기(버전·환경·최근 로그·설정, 비밀은 마스킹)
- 마스킹 미리보기와 누출 검사
- 로그 뷰어 검색·페이징(지금은 대용량 파일도 제한 없이 읽는다: `api/log_file_browser_api.py:245-252`)
- 장비 별칭(hostname/FQDN)으로 LLDP·파일명·인벤토리 조인 통일
- 고객사·벤더별 보고서 템플릿
- 워크스페이스 무결성 점검·자동 백업
- 실출력을 마스킹해 골든 픽스처로 승격하는 도구(`tools/`)

---

## 5단계 — 문서·릴리스 (v1.0.0)

### 5-1. 문서 정정·재배치 ✅

**결과** ✅ — 네 커밋으로 나눴다.
- **5-1a 재배치**(dc14ec1): 루트에는 README·CHANGELOG(와 EXE 에 딸려 가는 `실행방법.txt`)만 남기고 ARCHITECTURE·DEVELOPER_GUIDE·IMPROVEMENT_PLAN 을 `docs/`로, OPTIMIZATION_PLAN 을 `docs/history/`로 옮겼다(`git mv` — 내용은 그대로). README 링크와 코드·테스트 주석의 경로 언급을 고쳤다.
- **5-1b README·실행방법.txt**(9a15020): 작업 흐름을 사이드바 14개 메뉴(준비 3·실행 3·결과 5·기타 3)와 상단바 알림·상태바 작업 센터로 다시 썼다. 화면 설명을 코드와 대조해 고쳤다 — 실시간 경고는 '우측 하단 토스트'가 아니라 상단바 종 버튼(읽지 않은 수·가장 심각한 등급의 색)과 알림 창이다. `ai_settings.yaml`은 AI 제공자 우선순위, `labs/`는 이름만 레거시인 지금 쓰는 폴더(앱 폴더의 예전 `labs/`는 업그레이드가 한 번 옮길 뿐), 오탈자 '라라우팅'. 주요 특징·설정 파일·디렉터리 구조(앱 폴더와 사용자 데이터 폴더)·빠른 시작(EXE·데이터 폴더·무인 점검)·문제 해결 절. 지원 Python(3.12, D2)은 0-5 에서 이미 맞춰져 있었다. `실행방법.txt`는 3-1 에서 없앤 채점 파이프라인을 안내하고 있어 EXE 사용자용 짧은 안내로 바꿨고, `build_exe.py`가 `dist/AutoCheck/`의 AutoCheck.exe 옆에 복사한다.
- **5-1c DEVELOPER_GUIDE·ARCHITECTURE**(aed67c5): 가이드에 프런트엔드 규약(로드 순서, `call()`은 던지지 않는다, 반환 모양, 이스케이프, `onPageLeave`, 적응형 폴러와 `whenApiReady`), 빌드·릴리스(spec 과 비밀 검사, 체크리스트), Non-goals(OPTIMIZATION_PLAN 의 실측 결론) 절을 더하고 낡은 서술(고쳐진 위반, 캐시 없음, 잠금 없음, node 테스트 수)을 고쳤다. '테스트 스위트 없음'은 이미 지워져 있었다. ARCHITECTURE 는 의존 방향을 `tests/test_layering.py` 그대로(api → engine / ai_analysis → report → parsers → core), 없는 절을 가리키던 문장과 캐시·RESERVED 서술을 고치고, Known follow-ups 를 열린 것만으로 다시 썼다('13개' 항목은 이미 지워진 줄로 남아 있었다).
- **5-1d DATA_LAYOUT.md**(0ff4076): WORKSPACE_STRUCTURE 와 MIGRATION_GUIDE 를 합쳤다. 두 문서의 백업 위치 서술은 이미 같아져 있었는데, 백업이 두 종류(매일의 `backups/workspace_*.zip`, 레거시 마이그레이션 직전의 `migration_backup/`)라 헷갈리던 것을 'Backups' 절로 갈랐다. 4단계에서 생긴 파일(`realtime_state.json`, 고객사 `report_template.yaml`, `backups/`, `config/`의 파일들, `knowledge/`)을 배치도에 더했다.
- 테스트 1300 그대로(문서·주석만). EXE 빌드는 돌려 보지 않았다(실행방법.txt 복사).
- 찾았지만 문서 범위 밖이라 남긴 것(ARCHITECTURE Known follow-ups): 대시보드의 'Stage 진행률' 카드는 채점 파이프라인의 흔적으로 늘 비어 있다(API 가 빈 `stages`), `requirements.txt`의 pandas 는 앱 코드가 더 쓰지 않는다.

- **README**
  - 지원 Python 버전을 D2에 맞춘다.
  - 작업 흐름을 실제 사이드바 14개 메뉴 기준으로 다시 쓴다.
  - 화면 설명(실시간 감시 경고 표시, UI에 없는 PPTX 내보내기 등)을 현재 UI와 대조해 고친다.
  - `ai_settings.yaml`은 AI 제공자 순서 설정임을 밝힌다.
  - `labs/`가 레거시임을 밝힌다.
  - 오탈자('라라우팅')를 고친다.
- **DEVELOPER_GUIDE**
  - "테스트 스위트 없음"을 지우고 Testing 절을 다시 쓴다(pytest, node, CRLF 주의, bench 하네스).
  - 프런트엔드 규약 절을 추가한다(call 반환 규약, 이스케이프, 폴러, 로드 순서).
  - 빌드·릴리스 절을 추가한다.
- **ARCHITECTURE**
  - 의존 방향 서술을 정정한다.
  - Known follow-ups를 현행화한다('13개'라고 하면서 17개를 나열하는 부분 포함).
- **WORKSPACE_STRUCTURE + MIGRATION_GUIDE**: `DATA_LAYOUT.md` 하나로 합친다. 두 문서의 백업 위치 서술이 서로 다르다.
- **실행방법.txt**: EXE 사용자용 짧은 안내로 바꾼다(EXE에 동봉되므로). V0.5.0과 labs 언급은 삭제한다.
- **OPTIMIZATION_PLAN**: `docs/history/`로 옮기고, Non-goals 요약만 DEVELOPER_GUIDE에 남긴다.
- 루트에는 README와 CHANGELOG만 두고, 나머지 문서는 `docs/`로 모은다.

### 5-2. 릴리스 체크리스트(스크립트화) ✅

**결과** ✅(8f9f56b, a67b47a) — `python -m tools.release_check` 하나로 여섯 항목을 차례로 보고 `[통과]`·`[실패]`·`[건너뜀]`과 이유를 적는다. 종료 코드 0 = 릴리스해도 된다, 1 = 실패, 2 = 건너뛴 항목이 있다. 자식 프로세스(테스트·벤치마크·EXE)는 모두 임시 `AUTOCHECK_HOME`에서 돈다.
- **버전**: `VERSION` = CHANGELOG 맨 위 절의 제목(`## vX.Y.Z — …` — '미배포' 절이면 실패하고, 그 절의 예정 버전으로 올리라고 안내한다) = README 머리의 버전.
- **git**(덤): 작업 트리가 깨끗하고, 태그 `vX.Y.Z`가 아직 없거나 HEAD 를 가리킨다 — 있는데 다른 커밋이면 버전을 올리지 않은 것이다.
- **번들 비밀**: spec 이 담는 파일을 `tools/bundle_secret_check`로. spec 해석은 `spec_bundle_sources()` 한 곳(테스트도 그것을 쓴다).
- **테스트**: Windows 에서 pytest 전체 — 0 failed / 0 error / 0 skipped(node 가 없어 건너뛰었으면 그렇다고 알린다).
- **벤치마크**: `bench_log_analysis --check`의 종료 코드 그대로(퇴행·기준선 없음·판정 불가). 기준선은 PC 마다 달라 만들어 주지 않는다.
- **EXE**: `build_exe.py`로 빌드하고 `실행방법.txt`가 EXE 옆에 있는지 본 뒤, 새 `main.py --self-test <결과> --modules <목록>`(창을 띄우지 않는다)으로 번들 안에서 앱 모듈 전부와 앱이 import 하는 바깥 모듈을 하나씩 import 한다. 바깥 모듈은 소스를 AST 로 훑어 함수 안의 늦은 import 까지 모은다 — 창을 띄우는 것만으로는 수집·보고서·AI 모듈에 닿지 않는다. try/except ImportError 안의 것은 선택이지만 requirements.txt 의 패키지(netmiko)는 필수다.
- **실제로 돌린 결과**(2026-10-30, 이 PC): 번들 비밀 통과, 테스트 1310 passed / 0 skipped, **EXE 빌드와 번들 안 import 148개(앱 모듈 130 + 바깥 모듈 18) 통과 — spec 의 `unittest` 제외가 의존 패키지를 깨지 않는다는 것을 처음 확인했다(1-6 에서 미확인)**. 실패 셋은 지금 상태 그대로다: 버전(CHANGELOG 맨 위가 '미배포 (v0.6.0 예정)'), git(`v0.5.9` 태그는 원본 커밋), 벤치마크(이 PC 에 기준선이 없다 — 만들지 않았다). 첫 실행에서 버전 실패 문구가 VERSION(0.5.9)을 따라 '## v0.5.9'를 권하던 것을 고쳤다(a67b47a).
- 테스트 `tests/test_release_check.py`(10) — 진짜 pytest·벤치마크·빌드는 테스트 안에서 돌리지 않고(가짜 실행기), `main.py --self-test`만 소스로 진짜 돌린다. 1300 → 1310.
- 남은 것: 릴리스 자체(VERSION 0.6.0·CHANGELOG 제목·README 머리·태그)와 벤치마크 기준선은 사람이 정한다. 빌드한 EXE 의 화면은 띄워 보지 않았다(번들 안 import 만).

1. Windows에서 pytest 0 fail / 0 skip
2. `bench --check` 퇴행 없음
3. EXE 빌드 후 import 스모크 — `unittest` exclude가 의존 패키지를 깨지 않는지 함께 본다(1-6에서 미확인)
4. 번들 안에 비밀 파일 없음 — `AutoCheck.spec`이 빌드 때 `tools/bundle_secret_check.py`로 검사한다(1-6)
5. `VERSION` = CHANGELOG 제목

### 5-3. v1.0 완료 정의 — 대조 완료(실장비가 있어야 하는 둘이 남음)

**결과** — 다섯 기준을 코드·테스트와 하나씩 대조했다(2026-10-30). 코드로 메울 수 있는 빈틈은 메웠고(5-3a 83ce586, 5-3b 7c5ee61), 실장비가 있어야 하는 둘이 남았다.

| 기준 | 판정 | 근거 |
|---|---|---|
| 1. 외부 유출 차단 | 충족 | 내보내는 길마다 테스트로 고정했다 — 클라우드 AI 페이로드(`test_ai_transmission`·`test_ai_payload_guard`: 강제 마스킹·미리보기 승인·오류 로그의 키 가림), 알림(`test_notifications`), 앱 로그(`app_logger`의 `redact_secrets`), 내보내기(`test_export_safety`: 워크스페이스·프로젝트 zip 은 기본으로 비밀번호를 비운다), 진단 번들(`test_diagnostics`), 보고서(`test_report_data_protection`·`test_realtime_audit`), 마스킹 결과(`test_log_masking`·`test_masking_preview`: 누출 검사), EXE 번들(`test_bundle_secrets`), 화면 브리지(`test_secret_vault`·`test_inventory_secrets_js`: `has_*` 만) |
| 2. 데이터 보호 | 충족 | 이름변경(`test_profile_transactions`), 삭제 = 보관(`test_profile_archive` — 고객사·정기점검 삭제는 보관, 영구 삭제는 보관함에서 이름을 입력해야), 내보내기·가져오기(`test_export_safety`·`test_path_safety`·`test_profile_move` — 5-3a), 업그레이드(`test_config_upgrade`·`test_migration_paths`·`test_secret_vault`), 무결성 점검·백업(`test_workspace_care`). 점검 로그 파일과 지식베이스 문서의 '삭제'는 확인을 거친 영구 삭제로 남겨 두었다 — 보관은 정기점검 단위다 |
| 3. 실시간 감시 | 회귀 테스트 충족, **실장비 육안 확인 남음** | 재시작(`test_realtime_state_persistence`·`test_realtime_open_conditions`), 축출(`test_realtime_consistency` — 300건 상한에서 끝난·minor 경고부터 뺀다), history·중복(`test_realtime_flap_and_history` — 재발은 다시 열고 에코는 접되 센다), 감사 이력(`test_realtime_audit`). 남음: 실장비 SecureCRT 세션으로 경고 발생 → 복구 → 숨김 → 고정을 눈으로 확인(docs/history/OPTIMIZATION_PLAN '남은 확인' 포함) |
| 4. 문서와 기능 일치 | 충족(5-3a·5-3b 로 메움) | README 작업 흐름 = 사이드바 14개 메뉴(`test_docs_match_ui`), 공개 API 는 모두 화면이 부르고 화면의 호출은 모두 있다(`test_api_surface`), 브리지를 부르는데 아무도 부르지 않는 화면 함수가 없다(새 검사). D1 은 3-1 에서 끝났다. **D5 는 열려 있었다** — 부르는 곳 없는 옛 창 안에만 있던 정기점검 zip 내보내기·가져오기를 고객사/정기점검 창에 되살리고(가져온 것을 고객사 트리에 잇고, 같은 이름이면 '(가져옴)'을 붙여 data 폴더를 함께 쓰지 않게), 옛 창과 그 창만 쓰던 공개 API 다섯을 지웠다(5-3a). 대시보드의 채점 흔적 'Stage 진행률' 카드를 지웠다(5-3b) |
| 5. 테스트 | 녹색 충족, **실출력 골든 남음** | Windows 에서 1315 passed / 0 skipped(`tools/release_check`). 실출력 골든 테스트의 틀(`tools/promote_fixture`·`test_golden_fixtures`)은 있지만 픽스처가 0개다 — 저장소에 실제 장비 출력이 없다. 파서(+보고서 판정·구성도·로그 규칙)는 승격하면 바로 고정되고, 마스킹·데이터 계층·렌더러는 합성 입력 테스트(`test_log_masking`·`test_report_renderers` 등)만 있다 |

v1.0 을 선언하려면 남은 것(모두 사람의 몫):
1. 실장비 SecureCRT 세션으로 실시간 감시를 눈으로 확인(기준 3).
2. 실제 장비 출력, 특히 Cisco 를 `python -m tools.promote_fixture promote`로 골든 픽스처로 승격(기준 5, 4-5 의 Cisco 실장비 검증과 같은 일).
3. 릴리스 절차 — VERSION·CHANGELOG 제목·README 머리를 올리고, 벤치마크 기준선을 만들고, `python -m tools.release_check`가 0 으로 끝나면 태그.

1. **외부 유출 차단**: 외부 전송 페이로드·로그 파일·내보내기 산출물에 원문 비밀이 0건이다(테스트로 고정).
2. **데이터 보호**
   - 이름변경·삭제·내보내기·업그레이드 시나리오 테스트를 통과한다.
   - 삭제의 기본 동작은 보관이다.
3. **실시간 감시**
   - 재시작·축출·history·중복 경고의 회귀 테스트를 통과한다.
   - 실장비 세션으로 경고 발생 → 복구 → 숨김 → 고정을 육안으로 확인한다(OPTIMIZATION_PLAN '남은 확인' 포함).
4. **문서와 기능 일치**
   - README가 설명하는 모든 기능에 UI 진입점이 있다.
   - 진입점이 없는 기능은 코드에서 제거했다(D1·D5).
5. **테스트**
   - Windows에서 녹색이다.
   - 공백 영역(마스킹·파서·데이터 계층·보고서 렌더러)에 실출력 골든 테스트가 있다.

## 6단계 — 실장비(Arista) 사용 피드백 (v1.0.0)

v0.6.0 을 실장비 Arista 25대(EOS 4.23~4.36, 장비 목록 51대)에 써 본 피드백. Cisco 실장비는 없다. 실제 데이터 폴더는 읽기만
했고, 분석·보고서는 사본으로 돌렸다.

| # | 내용 | 커밋 |
|---|---|---|
| 6-1 | 장비 목록의 전체 선택 체크박스 — 다시 그릴 때마다 처리기가 쌓여 장비를 읽기 전 처리기가 체크를 풀었다 | d06c3a7 |
| 6-2 | 접속하면 장비 이름을 호스트명으로 바로 맞춘다(터미널·세션 점검, 옛 이름은 별칭). EOS 호스트명의 점을 자르던 것도 | 4f61b0b |
| 6-3 | 명령어 카탈로그를 메모장처럼(명령 다음 줄에 `!`, 설명 없음), 내보내기·불러오기 .txt, 폴더 열기 | 6046580 |
| 6-4 | 로그 글을 끌어서 고르면 바로 복사(pywebview 가 막던 글 선택을 로그 자리에서만 푼다) | 1a48901 |
| 6-5 | 보고서 탭 지연 — 규칙 판정 앞에서 필요 글자열로 거르기(판정 동일), 장비 판정 기억. 첫 13.7초 → 2.9초, 다시 4초 → 0.3초 | 76d606e |
| 6-6 | 원본로그분석·마스킹·보고서에서 점검 회차 고르기 | 40f56e0 |
| 6-7 | 실장비 로그로 판정 바로잡기 — 형식 줄 오탐(머리말·정상 접속 기록·쓰지 않는 포트), 놓친 것(PSU Power Loss·BGP 미성립·로그인 실패), 메모리 계산, 새 EOS 환경 명령 | 81e704c |
| 6-8 | **v1.0.0** — 사용자가 실장비로 정상 동작을 확인하고 1.0 으로 올렸다(태그 v1.0.0). 독점 라이선스(`LICENSE` — 영문·한국어, All Rights Reserved)와 저작권자 표시(README, 앱 하단 상태바, `AutoCheck.exe` 속성, EXE 폴더의 `LICENSE`·`실행방법.txt`) | 릴리스 커밋 |

남은 것: 이미 있는 고객사 카탈로그에 `show system environment cooling`·`temperature` 를 더해야 FAN·온도를 판정한다(카탈로그의
'기본 카탈로그 명령'에서 한 번씩 누른다). Flash 항목은 카탈로그에 명령이 없어 늘 미수집이다. `down notpresent`·`down dormant`
인터페이스는 '확인필요'로 남겨 두었다(설정 잔재인지 장애인지 현장 판단). 실출력 골든 픽스처는 아직 0개다 — 고객 로그를 저장소에
넣는 일이라 승인이 필요하다.

---

## 원칙과 하지 않을 것

**지킬 것**
- 버그를 고치기 전에 재현 테스트부터 쓴다.
- 픽스처는 실제 출력(마스킹본)을 쓴다. 상상으로 만든 벤더 출력은 금지한다. CHANGELOG에 따르면 그렇게 만든 기존 BGP/OSPF 파서는 실출력을 한 줄도 읽지 못했다.
- 테스트는 Windows에서 돈다.
- OPTIMIZATION_PLAN의 Non-goals를 유지한다: 정규식 alternation 통합, 리터럴 게이트, script 번들링, collector 병렬 구조 변경 등.

**하지 않을 것**
- 전면 재작성이나 프레임워크 교체(React 등). 동작 중인 실무 도구이므로 항목별 독립 커밋으로 진행한다.
- 새로운 전역 '현재 X' 포인터(DEVELOPER_GUIDE 규칙 6).
- 근거 없는 시각 포맷 일괄 통일. 저장 데이터의 날짜 누락만 고친다.

---

## 부록 A — Cisco 지원 공백 (4-5의 작업 목록)

> 4-5 에서 수집·명령·드라이버(NX-OS·vPC·HSRP)·구성도·보고서 항목과 파서 중 etherchannel 줄·err-disabled·counters 열을 처리했다. 남긴 것은 4-5 결과의 '미룬 것' 참고(비교기 항목은 3-1 에서 비교기와 함께 퇴역했다).

### 수집
- netmiko `device_type="arista_eos"`로 고정돼 있다(`engine/collector.py:96` ✔).
- platform 값도 고정돼 있다(`:242`).
- 드라이버의 `netmiko_device_type`을 쓰지 않는다.
- enable secret 필드가 없다.

### 명령
- 카탈로그가 벤더 구분 없는 리터럴이라 EOS 전용 명령이 그대로 나간다.
- 예: `show processes top once`, `show port-channel dense`, `show mlag`, `show ip virtual-router`, `show interfaces vxlan`, `… | grep`

### 드라이버
- IOS/IOS-XE만 있고 NX-OS가 없다.
- mlag·varp가 None이다(vPC·StackWise 미매핑).
- HSRP 체크가 없다.

### 파서
- Cisco용 등록이 0건이다(`plugins/parsers/registry.py:22-49`).
- 등록하더라도 다음 형식을 읽지 못한다.
  - ACL `Extended/Standard IP access list` 헤더
  - etherchannel 줄 형식
  - `err-disabled` 상태
  - counters 열의 의미 차이
  - NTP 서버 추출

### 비교기
- STP 기본 우선순위가 32768로 고정돼 있어 PVST+에서 오판한다(`engine/comparators/stp.py:7`).

### 실시간 감시
- `(config-if)#` 문맥을 잃는다(2-10).
- `%LINK-3-UPDOWN`과 NX-OS ETHPORT 패턴이 없다(2-11).

### 구성도
- LLDP와 Arista MLAG만 지원한다(`engine/topology_builder.py:100,106`).
- Cisco 기본값인 CDP와 vPC를 지원하지 않는다.

### 보고서
- 모델·S/N·OS 정규식이 Arista 전용이다(`engine/inspection_report_builder.py:290-311`).
- `dell|hpe?`에 걸리면 서버로 분류된다(`report/inspection_pdf.py:80-83`).

## 부록 B — 채점 파이프라인을 재건할 경우(D1 대안)의 수정 목록

1. **bulk 출력 분할**
   - 수집 직후 프롬프트 기준으로 명령별로 나눈다.
   - 명령을 정규화해서 매칭한다(`engine/collector.py:132-140` ✔, `pipeline/steps.py:86-94` ✔).
   - 카탈로그와 드라이버의 명령 문자열이 다르다. 예: `show logging`↔`show log`, `port-channel dense`↔`summary`(`plugins/vendors/arista.py:36-43`).
2. **이력 diff**
   - 저장 전에 `load_previous`(목록[-2])를 불러, 직전이 아니라 2회 전 회차와 비교한다(`pipeline/steps.py:188` ✔, `engine/history.py:43-50` ✔).
   - 파일명이 분 단위라 같은 분에 두 번 실행하면 덮어쓴다(`:15` ✔).
   - 쓰기가 비원자적이다(`:29-30` ✔).
3. **미수집·0건·파싱실패 구분**
   - 장비를 수집하지 못해도 FAIL로 나온다(`comparators/vlan.py:10-17`, `stp.py:50-57`).
   - 수집됐는데 0건이면 UNKNOWN이 된다(`ospf.py`, `lacp.py`, `acl.py`).
   - `% Invalid input`은 FAIL로 처리된다.
4. **파서와 실출력 불일치**
   - OSPF는 VRF 열 때문에 항상 0건이다(`parsers/show_routing_neighbor.py:7`).
   - BGP 레이아웃이 맞지 않는다.
   - STP는 `MST0`를 인식하지 못하고, sys-id-ext가 더해진 우선순위로 오판한다(`parsers/show_spanning_tree.py:9-11`).
5. **신규 프로젝트 판정**
   - KeyError가 난다(`rule_engine/rules.py:28-29`).
   - inspection 판정은 비교 없이 고정값을 쓴다(`:70-81`).
6. **Step 실패 격리**
   - 예외 하나가 파이프라인 전체로 전파된다(`pipeline/pipeline.py:27-33`).
   - 미등록 vendor면 전체가 중단된다(`plugins/vendors/base.py:35-39`).
   - 알람 핸들러 하나가 실패하면 뒤 단계가 실행되지 않는다(`alarm/base.py:39-42`).
7. **Health Score 왜곡**(`core/health_score.py:24-31, 44-46, 61-66`)
   - override 키가 실제 check_id와 맞지 않는다.
   - 가상 장비가 평균에 들어간다.
   - UNKNOWN도 감점된다.
8. **Findings AI 요약**
   - `user_approved_cloud=True`로 호출하는 곳이 0이라 항상 규칙 기반으로만 동작한다.
   - 키 불일치로 KeyError가 난다(`ai_analysis/findings_analyzer.py:29`).
