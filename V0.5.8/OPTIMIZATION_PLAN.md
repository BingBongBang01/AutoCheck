# AutoCheck V0.5.8 최적화 계획

## 요약

**0~2단계 완료.** 앱의 최대 CPU 소비처였던 로그 판정 엔진(`engine/log_rule_engine.py`)이 **2.72배 빨라졌다**(`analyze_text` 70.2 → 25.8 ms). 시작 경로에서 미사용 의존성 3개(pandas/paramiko/openpyxl)를 걷어냈고, 유휴 폴링 빈도를 1/5로 줄였다. 테스트 0개에서 **160개**가 됐다.

이 계획의 가장 중요한 발견은 **가장 그럴듯한 최적화가 실제로는 퇴행**이었다는 것이다. "정규식 31개를 단일 alternation으로 합친다"는 실측 2.6배 느림(Python `re`가 패턴별 리터럴 접두사 최적화를 잃고 모든 위치에서 백트래킹한다). 성립한 해법은 **줄 단위 메모화**였고, 그것도 **회차 전체가 메모를 공유해야** 의미가 있다 — 파일 단위 메모는 1.05배로 사실상 무의미하다.

3단계는 **측정으로 절반이 걸러졌다**. 3-3(워크스페이스 단일 스캔)은 약 2배 개선으로 적용했고, **3-4와 3-5는 실측 이득이 1 ms 대라 폐기**했다(3-5는 점검 직후 30초 낡은 수치라는 기능 후퇴까지 따라온다). 남은 실질 항목은 두 개다:

- **3-2 반복 파싱 캐시** — 대시보드 비용의 99%가 파싱이고, 같은 로그를 한 흐름에서 4~5회 재파싱한다. 2단계 메모가 이미 2.5~3.3배 줄였지만 구조적 중복은 남아 있다.
- **3-1 실시간 폴링 payload** — 장비 30대에서 723 KB/s. 계산은 1.3 ms로 싸고 문제는 전송량과 DOM 전면 재구축이다. 리스크 HIGH(세 패널의 스냅샷 원자성)이므로 신중히 진행해야 한다.

---

## 측정 기준선

| 항목 | 실측값 | 측정 방법 | 플랫폼 |
|---|---|---|---|
| `analyze_text()` 처리 속도 | 47.8 us/line (12,800줄 → 611ms) | 합성 Arista 로그 + `cProfile` | 이번 세션(Linux) |
| `analyze_text()` 처리 속도 | 15.7 us/line (12,800줄 → 201ms) | 동일 코퍼스 | 이전 측정(Windows) |
| `re.Pattern.search` 비중 | tottime의 54%, 호출 296,002회 | `cProfile` sort=tottime | 이번 세션(Linux) |
| `match_signature()` (31 regex) | 191 ms / 9,600줄 | 단계별 계측 | 이번 세션(Linux) |
| `find_keyword()` (17 regex) | 103 ms / 9,600줄 | 단계별 계측 | 이번 세션(Linux) |
| `suppressor.check()` (구조 억제) | 59 ms / 9,600줄 | 단계별 계측 | 이번 세션(Linux) |
| `counter_row()` | 2 ms / 9,600줄 | 단계별 계측 | 이번 세션(Linux) |
| `evaluate()` 합계 | 255 ms / 9,600줄 (단축평가로 부분합보다 작음) | 단계별 계측 | 이번 세션(Linux) |
| signature 규칙 수 | JSON 33 entry 중 **31개만 컴파일** (2개는 `_comment` 전용) | `log_rules.json` 파싱 | 이번 세션 |
| signature 중 `scope` 보유 | **0 / 31** | 컴파일 결과 검사 | 이번 세션 |
| `AppPaths.crt_log_root()` | 9.8 us/call (매 호출 mkdir) | 3,000회 반복 | 이번 세션(Linux) |
| `AppPaths.data_root()` / `config_root()` / `logs_root()` | 9.6 / 9.3 / 9.2 us/call | 3,000회 반복 | 이번 세션(Linux) |
| `AppPaths.app_root()` (캐시됨, 비교군) | 0.1 us/call | 3,000회 반복 | 이번 세션(Linux) |
| `AppPaths.crt_log_root()` | 64.8 us/call | 2,000회 반복 | 이전 측정(Windows) |
| `RealtimeMonitor.state(tail=160)` 장비 4대 | 0.91 ms/poll, payload 94 KB → 118 KB/s | 경고 300건 상태 구성 | 이번 세션(Linux) |
| 〃 장비 12대 | 1.07 ms/poll, payload 243 KB → 303 KB/s | 〃 | 이번 세션(Linux) |
| 〃 장비 30대 | 1.29 ms/poll, payload 578 KB → 723 KB/s | 〃 | 이번 세션(Linux) |
| `api/*` 믹스인 전체 import | 약 1.35 s | `python -X importtime` | 이전 측정(Windows) |
| ├ pandas | 665 ms (`engine/command_catalog.py:18`) | 〃 | 이전 측정(Windows) |
| ├ paramiko | 339 ms (`engine/ssh_client.py:13`) | 〃 | 이전 측정(Windows) |
| └ openpyxl | 269 ms (`report/inspection_excel.py:25`) | 〃 | 이전 측정(Windows) |
| `import webview` | 200 ms | 단독 계측 | 이전 측정(Windows) |
| `ensure_requirements()` 패키지 스캔 | 34 ms | 단독 계측 | 이전 측정(Windows) |
| 테스트 수 | 0개 → **170개 통과**(0·1·2단계 + 3-3 완료) | `python -m pytest tests/ -q` | 이번 세션 |

> Linux 재측정이 Windows 대비 3배 느린 것은 머신 차이다. **절대값이 아니라 비율과 프로파일 형태가 일치**하는 것이 확인 포인트이며, 두 환경 모두 `re.search`가 지배적(54~55%)이고 호출 횟수(296,002)까지 동일하다.

### 0단계 하네스 기준선 (장비 8대, 시드 7, 반복 7회, Linux)

`python -m tools.bench_log_analysis --save-baseline`으로 저장한 값이다. 이후 모든 항목은 `--check`로 이 기준선과 비교한다.

| 지표 | 값 |
|---|---|
| 코퍼스 | 전체 1,792줄 / 판정 대상 1,760줄 / 고유 472줄 (중복률 73.2%) |
| `analyze_text` | 68.2 ms (38.7 us/판정줄) |
| `match_signature` | 34.9 ms (`evaluate` 62.0 ms의 56%) |
| `find_keyword` | 21.6 ms |
| `suppressor.check` | 12.6 ms |
| `counter_row` | 0.3 ms |
| 실시간 payload (장비 4 / 12 / 30대) | 102.6 / 267.0 / 639.1 KB → 128 / 334 / 799 KB/s |
| 반복 편차 | 1.5% (재현성 양호) |

### 메모화 이득 — 장비 수에 따른 실측 곡선

**이전 세션의 6.9배/9.0배는 과대평가였다.** 워밍된 메모를 3회 평균에 섞어 재서 나온 값이다. 0단계 하네스로 파일 단위와 회차 단위를 분리해 다시 재면:

| 장비 수 | 메모 히트율 | `match_signature` 파일단위 | 회차공유 | `find_keyword` 파일단위 | 회차공유 |
|---|---|---|---|---|---|
| 1 | 21.4% | 1.05× | 1.05× | 1.08× | 1.09× |
| 4 | 65.7% | 1.08× | **2.52×** | 1.05× | **2.59×** |
| 8 | 73.2% | 1.08× | **3.21×** | 1.07× | **3.51×** |
| 20 | 77.7% | 1.05× | **3.86×** | 1.09× | **4.28×** |

읽는 법: 한 장비 로그 안에서는 줄이 대부분 고유하다(포트 번호가 다르다) — 그래서 파일 단위 메모는 쓸모가 없다. 이득은 **장비 사이의 반복**(포트 48개 표가 장비마다 똑같다)에서 나오므로 메모가 회차 전체를 살아남아야 한다. `RuleEngine`이 프로세스 싱글턴(`get_engine()`)이라 메모를 그 인스턴스에 두면 이 조건이 자동으로 충족된다.

---

## 0단계 — 안전망 ✅ 완료

판정 로직을 건드리는 모든 항목의 선행 조건이다. 이 단계 없이 2단계로 가면 안 된다.

실행: `pip install -r requirements-dev.txt` → `python -m pytest tests/ -q` → `python -m tools.bench_log_analysis --check`

### 0-1. 판정 characterization 테스트 ✅

- **파일**: `tests/test_log_rule_engine_golden.py`(20 tests), `tests/test_analyze_text_snapshot.py`(11 tests)
- **고정한 것**:
  - 오탐 5건이 억제된 상태(`"Number of table drops : 0"`, `"hitless-reload-down Disabled 300"`, `"   U - In Use    D - Down"`, 안내문, 인라인 0 카운터)
  - 미탐 4건이 검출되는 상태 + 그 `rule_id`/`severity`까지(`ntp_not_synced/major`, `command_rejected/minor` ×2, `unexpected_reload/major`)
  - 맥락 의존 경로: running-config 구간 억제, counters 표의 0/비0 칸 구분(`counter_nonzero`, 사유에 `FCS=12`·`Symbol=3` 포함), 빈 줄에서 헤더 리셋
  - syslog facility 심각도 매핑(`-5-` → major, `-2-` → critical)
  - `analyze_text()` 전체 스냅샷: 원시 18건(`counter_nonzero` 6 + `link_state_down` 12) → 스로틀/상관분석 후 3건, `repeat` 합이 원시 건수를 보존, 복합 finding `link_flapping/critical`
  - 규칙 파일 형태(signature 33 entry / suppressions 10 / correlation 10 / keywords 17 / counter_columns 15)
- **부수 발견(테스트로 고정)**: signature 33개 중 **2개가 `_comment` 전용**이라 `pattern` 키가 없고, `_compile_patterns()`가 `KeyError`를 삼켜 조용히 31개만 컴파일됐다. 정규식 오타도 같은 방식으로 규칙을 사라지게 한다 → **1-4 에서 수정됨**(주석은 의도적으로 건너뛰고 오타는 경고).
- **가장 중요한 테스트**: `test_no_signature_declares_scope` — 31개 signature 전부 `scope=None`임을 못박는다. 이것이 **2-1 메모화의 안전성 근거**이고, 누군가 `scope`를 가진 규칙을 추가하면 실패해서 메모 게이트를 확인하게 만든다.
- **작성 중 수정한 것**: `test_counter_table_detects_only_nonzero_cells`가 처음 실패했다. `ctx.feed()`가 헤더 줄을 소비하지 않아(구분선만 소비하며 그때 직전 줄을 헤더로 기억한다) 인덱스가 밀린 것으로, 코드가 아니라 테스트의 오해였다. 0단계 원칙대로 테스트를 고쳤다.
- **리스크**: LOW / **공수**: S(완료)

### 0-2. 재현 가능한 벤치마크 하네스 ✅

- **파일**: `tools/synthetic_log.py`(코퍼스 생성), `tools/bench_log_analysis.py`(측정/비교), `tests/test_bench_harness.py`(38 tests). 기준선(`tools/bench_baseline.json`)은 gitignore — 머신마다 절대값이 3배 이상 다르므로 각자 `--save-baseline` 으로 만든다.
- **기능**: 고정 시드 합성 로그(장비 수·중복률 파라미터) + `analyze_text` 및 단계별 타이밍 + 메모 이득(파일 단위 / 회차 공유 분리) + `RealtimeMonitor.state()` payload를 장비 4/12/30대로 측정. `--save-baseline` / `--check`(임계값 10% 초과 퇴행 시 exit 1) / `--json`.
- **대표값은 평균이 아니라 최소**를 쓴다 — 다른 프로세스의 방해는 시간을 늘리기만 하므로 최소값이 재현성이 높다. 편차는 `(중앙값 − 최소)/최소`로 재서 단일 이상치에 흔들리지 않게 했다(처음엔 `(최대 − 최소)`를 써서 재현성 양호한데도 58.8% 경고가 떴다).
- **종료 코드**: `0` 퇴행 없음 / `1` 퇴행 / `2` 기준선 없음 / `3` **판정 불가**(머신이 시끄러워 비교 무의미).
- **작성 중 고친 결함 6건** — 하네스 자체가 틀리면 이후 모든 주장이 무의미하므로 전부 기록한다:
  1. 중복률 역산을 닫힌 형태 근사식으로 하니 목표 0.68에 0.60이 나왔다(장비명이 명령 줄에 박혀 반복 블록도 완전히 동일하지 않다). 실측 기반 이분탐색으로 교체 → 오차 0.01 이내.
  2. 메모 웜 측정이 순수 dict 조회만 재서 511배라는 무의미한 값을 냈다. 실제 구현과 같은 래퍼(함수 호출 + dict 조회)로 교체.
  3. 퇴행 방향 판정이 `endswith("_speedup")`이라 `match_signature_memo_speedup_run` 같은 이름이 조용히 검사에서 빠졌다 — **메모 이득 퇴행이 감지되지 않는 버그**였다. 부분 문자열 매칭으로 교체.
  4. 중앙값과 최소값을 둘 다 비교해서, 코드 변경이 전혀 없는데 중앙값만 +12.6%로 튀어 퇴행이 보고됐다. 대표값은 최소값 하나로 하고 중앙값은 정보용으로 내렸다.
  5. 상대 변화만 봐서 `counter_row_ms`(0.3 ms)가 0.26 ms 흔들린 것이 +83.9% 퇴행으로 보고됐다. **절대 잡음 하한**(시간 2 ms, payload 5 KB, 배수 0.1배)을 추가해 상대·절대 조건을 둘 다 넘겨야 퇴행으로 본다.
  6. 신뢰도 신호를 `analyze_text` 편차만으로 잡으니 뒤쪽 측정 블록의 경합을 놓쳤다(편차 1.6%인데 `find_keyword_ms` +53%). 반대로 전체 블록의 `max`로 바꾸니 0.3 ms짜리 작은 블록 때문에 조용한 실행에서도 항상 판정 불가가 됐다. **전체 블록 편차의 p75**로 정착(조용한 실행 실측: median 1.2% / p75 2.9% / max 8.1%).
- **잡음 적응 임계값**: 유효 임계값 = `max(10%, 2.5 × 이번 실행의 편차 p75)`. 고정값 하나로는 두 실패를 동시에 피할 수 없다 — 10%로 두면 살짝 붐빈 실행(p75 6.2%)에서 +13.5%가 오탐이 되고, 20%로 올리면 진짜 15% 퇴행을 놓친다. "변화가 이 실행 자신의 잡음보다 도드라지는가"를 기준으로 삼는다.
- **검증**: 기준선을 인위적으로 조작해 3개 지표(시간 +98.5%, 메모 이득 −50.8%, payload +299.9%)가 모두 포착되고 exit 1이 되는지 확인. 조용한 실행 편차 p75 3.5%.
- **알려진 한계(정직하게 기록)**: 공유 컨테이너에서 8회 연속 `--check`를 돌린 실측에서 **7회 정상, 1회 오탐**(`find_keyword_memo_speedup_run` −34.9%). 이 환경의 CPU 경합이 17% 임계값도 넘긴 경우다. **퇴행이 보고되면 먼저 한 번 더 재서 재현되는지 확인할 것.** 개발 PC(조용한 Windows)에서는 더 안정적일 것으로 기대하지만 확인되지 않았다.
- **리스크**: LOW / **공수**: S(완료)

---

## 1단계 — 무위험 즉시 적용 ✅ 완료

판정 결과·기능 동작을 전혀 바꾸지 않는 항목만 모았다.

**적용 결과 요약**

| 항목 | 실측 결과 | 검증 |
|---|---|---|
| 1-1 무거운 의존성 지연 | 16개 믹스인 전부가 pandas/paramiko/openpyxl **0개 설치 상태**에서 import (변경 전 `api.base`·`api.inspection_report_api` 실패) | `tests/test_startup_imports.py` (20) |
| 1-2 `AppPaths` 캐시 | 9.8 → **0.22 us/call** (45배). 수용 기준 1 us 충족 | `tests/test_app_paths.py` (39) |
| 1-3 폴러 백오프 | 유휴 브리지 호출 초당 2회 → **0.4회**. `setInterval` 겹침 결함도 함께 제거 | `tests/test_adaptive_poller.py` (8) |
| 1-4 규칙 컴파일 진단 | 정규식 오타가 조용히 사라지지 않는다 | 골든 테스트 4건 추가 |

시작 시간 절감폭 자체(약 1.27초 기대)는 **Windows에서 `-X importtime`으로 재측정해야 한다** — 이 환경에는 pywebview가 없어 창이 뜨기까지의 시간을 잴 수 없다.

### 1-1. 무거운 의존성 지연 import

- **대상 파일**: `engine/command_catalog.py:18`, `engine/ssh_client.py:13`, `report/inspection_excel.py:25-27`, `engine/inspection_report_builder.py:29`
- **지금 동작**: 모듈 최상단에서 eager import한다. `api/*` 믹스인 조합 시점에 전부 로드된다.
  - `pandas`는 `engine/command_catalog.py:30`의 `pd.read_excel(EXCEL_DEFAULT_PATH)` **단 한 곳**에서만 쓰인다.
  - `paramiko`는 SSH 세션을 실제로 열 때만 필요하다.
  - `openpyxl`은 Excel 보고서를 생성할 때만 필요한데, `api/inspection_report_api.py` → `engine/inspection_report_builder.py` → `report/inspection_excel.py` 체인으로 시작 시 끌려온다.
- **변경**: 세 import를 사용 지점 함수 내부로 옮긴다. `command_catalog.py`는 이미 같은 파일 134/157행에서 `import openpyxl`을 함수 내부에서 하는 선례가 있으므로 그 패턴을 따른다.
  ```python
  # engine/command_catalog.py — 최상단 import pandas 제거
  def load_default_catalog():
      import pandas as pd          # 이 함수만 필요
      df = pd.read_excel(EXCEL_DEFAULT_PATH)
  ```
- **예상 이득**: 시작 시 최대 **약 1.27초 단축**(pandas 665 + paramiko 339 + openpyxl 269, 이전 측정(Windows) 기준). 단, 이 셋 사이에 공유 전이 의존성(numpy 등)이 있어 합산값보다 작을 수 있다 — 1단계 적용 후 `-X importtime`으로 재측정해 실제값을 기록한다.
- **리스크**: LOW — 지연 import는 첫 사용 시점에 비용이 옮겨질 뿐이다. 다만 첫 SSH 접속/첫 Excel 내보내기에서 수백 ms 지연이 체감될 수 있으므로, 해당 UI 동작에 이미 진행 표시가 있는지 확인한다(수집·내보내기 모두 `JobRunner` 백그라운드 경로라 진행바가 있다).
- **공수**: S
- **검증 방법**: `python -X importtime main.py` 전후 비교. `python -c "import api.base"`가 pandas를 로드하지 않음을 `sys.modules` 확인으로 단정.
- **선행 조건**: 없음

### 1-2. `AppPaths` 디렉터리 생성 결과 메모화

- **대상 파일**: `core/paths.py`
- **지금 동작**: `data_root()` / `labs_root()` / `config_root()` / `history_root()` / `logs_root()` / `crt_log_root()`가 **매 호출마다** `_ensure()` → `mkdir(parents=True, exist_ok=True)` 시스콜을 낸다. 호출당 9.2~9.8 us(Linux) / 64.8 us(이전 측정, Windows). 비교군인 `app_root()`는 캐시되어 0.1 us다.
  - 폴링 경로에서 반복 호출된다: `api/log_analysis_run_api.py`의 `get_realtime_monitor_state()`(0.8초 주기)가 `AppPaths.crt_log_root()`를 호출하고, `get_realtime_baseline_status()`도 별도로 호출한다.
- **변경**: 이미 만들어진 경로를 클래스 레벨 set에 기록하고 두 번째 호출부터는 mkdir을 건너뛴다.
  ```python
  _ensured: set = set()

  @staticmethod
  def _ensure(path: Path) -> Path:
      key = str(path)
      if key not in AppPaths._ensured:
          path.mkdir(parents=True, exist_ok=True)
          AppPaths._ensured.add(key)
      return path
  ```
- **예상 이득**: 해당 호출들이 9.x us → 0.1 us 수준(약 90배). 절대 절감폭은 작다(폴링 1회당 수십 us) — **이 항목의 가치는 CPU 절감보다 "0.8초마다 디스크에 쓰기 시도를 하지 않는다"는 점**이다. 과대평가하지 않는다.
- **리스크** LOW — 다만 **외부에서 폴더를 삭제하면 자동 재생성되지 않는다**는 동작 변화가 있다. 사용자가 탐색기에서 `CRTlog`를 지우는 시나리오가 실재하므로, 쓰기 직전 경로(`save_snapshot`, `open_in_file_explorer`)는 이미 각자 `makedirs`를 호출하고 있는지 확인하고 없으면 추가한다.
- **공수**: S
- **검증 방법**: 반복 호출 타이밍 재측정. 앱 실행 중 `CRTlog` 삭제 후 실시간 감시가 정상 동작하는지 수동 확인.
- **선행 조건**: 없음

### 1-3. 유휴 시 항상 도는 1초 폴러 정리

- **대상 파일**: `web_ui/js/analysis-progress.js:114`, `web_ui/js/workspace.js:263`
- **지금 동작**: 두 파일이 각각 `setInterval(..., 1000)`으로 **작업이 하나도 없어도** 무조건 `get_analysis_jobs_status()` / `get_workspace_job_status()`를 호출한다. 탭이 열려 있지 않아도 돈다. 실시간 감시 패널(`realtime-monitor-panel.js:272`)은 대조적으로 카드가 사라지면 스스로 `clearInterval`한다.
- **변경**: 두 폴러에 유휴 백오프를 넣는다. 응답에 `running` 상태가 없으면 주기를 1초 → 5초로 늘리고, 작업 시작(사용자가 버튼을 누른 시점) 또는 `running` 감지 시 1초로 복귀한다. `realtime-monitor-panel.js`의 자기 정리 패턴을 참고한다.
- **예상 이득**: 유휴 상태 pywebview 브릿지 호출이 초당 2회 → 0.4회. **추정 — 브릿지 호출 1회당 비용을 측정하지 않았다.** 실제 이득 확인은 1-3 적용 후 유휴 상태 CPU 사용률 비교로 한다.
- **리스크**: LOW — 작업 완료 감지가 최대 5초 늦어질 수 있다. 완료 시 토스트를 띄우는 코드이므로 사용자가 체감할 수 있다. 버튼 클릭 시 즉시 1초 모드로 전환하면 실질 영향은 없다.
- **공수**: S
- **검증 방법**: 분석/리포트 작업을 실행해 진행바와 완료 토스트가 정상 동작하는지 확인.
- **선행 조건**: 없음

### 1-4. 규칙 컴파일 실패 진단 ✅ (계획 수정됨)

- **대상 파일**: `engine/log_rule_engine.py` (`_compile_patterns`)
- **지금 동작**: `except Exception: continue` 가 모든 실패를 삼켜, 주석 엔트리와 정규식 오타가 구별되지 않았다.
- **계획을 바꾼 이유**: 원래는 주석 엔트리를 배열 밖으로 옮기려 했다. 내용을 확인하니 **두 주석은 위치가 곧 의미**였다 — index 4 는 "앞의 4개 서명이 뒤쪽 일반 규칙(`interface_line_down` / `mlag_peer_problem` 등)보다 먼저 있어야 한다"는 순서 제약을 그 지점에서 설명한다. 배열 순서가 곧 우선순위이므로 밖으로 옮기면 어느 규칙에 대한 설명인지 알 수 없게 된다.
- **실제 변경**: 주석 엔트리(`pattern` 키 없음)는 **의도적으로 조용히** 건너뛰고, 정규식 컴파일 실패는 규칙 id 와 함께 경고를 남긴다. `scope` 컴파일 실패도 경고한다 — `scope` 가 `None` 이 되면 규칙이 모든 명령에 적용되어 좁히려던 규칙이 넓어지고 오탐이 늘어난다(규칙 자체는 살린다). `print` 를 쓰는 이유는 `core/app_logger.py` 의 `install_print_capture()` 가 이를 앱 로그로 캡처하기 때문이다.
- **이득**: 성능 이득 없음. **조용한 규칙 유실을 드러내는 것**이 목적이다 — 규칙 하나가 신호 없이 빠지면 그 규칙이 잡던 장애를 앱이 '이상 없음'으로 보고한다. 점검 도구에서 가장 나쁜 실패다.
- **리스크**: LOW / **공수**: S(완료)
- **검증**: 주석은 경고 없이 건너뛰는지, 오타 `pattern`/`scope` 가 id 와 함께 보고되는지, 실제 `config/log_rules.json` 이 경고 없이 전부 컴파일되는지(실패하면 지금 어떤 규칙이 빠지고 있다는 뜻).

---

## 2단계 — 핫패스 최적화 ✅ 완료

`evaluate()`가 앱 최대 CPU 소비처였다. 0단계 안전망 위에서 진행했다.

**누적 결과** (장비 8대 코퍼스, 매 측정 전 메모 비움 — 즉 낙관적이지 않은 수치)

| 지표 | 0단계 기준선 | 2-1 후 | 2-2 후 | 누적 |
|---|---|---|---|---|
| `analyze_text` | 70.2 ms | 33.3 ms | **25.8 ms** | **2.72배** |
| `us/판정줄` | 39.9 | 18.9 | **14.7** | 2.72배 |
| `evaluate` | 63.1 ms | 26.7 ms | **20.1 ms** | 3.14배 |
| `match_signature` | 34.2 ms | **10.9 ms** | 10.9 ms | 3.14배 |
| `find_keyword` | 22.4 ms | **6.2 ms** | 6.2 ms | 3.61배 |
| `suppressor.check` | 11.8 ms | 11.8 ms | **4.95 ms** | 2.38배 |

### 2-1. 줄 단위 메모화 ✅

- **대상**: `engine/log_rule_engine.py` (`SignatureMatcher.match_signature`, `RuleEngine.find_keyword`)
- **적용**: 줄 문자열을 키로 메모. `RuleEngine` 인스턴스에 붙여 **회차 전체가 공유**한다(`get_engine()`이 프로세스 싱글턴).
- **안전 게이트**: 서명 31개 전부 `scope=None`이라 순수 함수다. `scope`를 가진 서명이 추가되면 `_memo_safe`가 `False`가 되어 `set_memo_enabled(True)`로도 켜지지 않는다.
- **회차 공유 이득**: 장비 8대 **3.11배** / 20대 **3.76배**(`match_signature`), 3.47 / 4.26배(`find_keyword`). 수용 기준 3.0/3.3배 충족.
- **상한**: 5만 엔트리. 넘으면 더 담지 않되 비우지 않는다(비우고 다시 채우기를 반복하면 캐시 없는 것보다 느려진다).
- **추가 API**: `set_memo_enabled()` / `clear_memo()` / `memo_stats()`. 운영 코드는 쓰지 않지만, 이게 있어야 "메모가 판정을 바꾸지 않는다"를 같은 엔진으로 대조할 수 있다.
- **리스크**: MEDIUM(실현) / **공수**: M

### 2-2. 억제 계층 메모화 ✅ (계획 수정됨)

- **계획을 바꾼 이유**: 원래 계획은 "`suppressor.check` 이중 호출을 한 번으로 합치기"였다. 착수 전 실측하니 **그 2차 호출은 1,760줄 중 8줄(0.5%), 0.10 ms**였다. 억제는 오탐 방지의 핵심이라 MEDIUM 리스크인데 이득이 0.1 ms — 감당할 가치가 없다. 계획이 "코퍼스 의존적이므로 먼저 재고 착수 여부를 정한다"고 해 둔 대로 그 변경은 하지 않았다.
- **대신 한 것**: 2-1로 서명/키워드가 싸지면서 `suppressor.check` **1차 호출**(모든 줄에 대해 도는 호출)이 `evaluate`의 약 45%를 차지하게 됐다. suppression 규칙도 `scope` 보유가 0/10이라 그 호출 형태(`keyword=None, include_config_scope=False`)는 줄만의 순수 함수였다 → 메모화. **11.79 → 4.95 ms (−58%)**.
- **정확성 세부**: `ctx=None` 호출은 메모하지 않는다 — 그 경우 `is_legend` 검사를 건너뛰어 결과가 달라지므로(범례 줄이 억제되지 않는다) 같은 키로 캐시하면 한쪽이 다른 쪽의 답을 받는다. 테스트로 고정했다.
- **리스크**: MEDIUM(실현) / **공수**: M

### 2-3. `counter_value` 정규식 사전 컴파일 — ❌ 하지 않음(실측 근거)

- 실제 판정 경로에서 `counter_value`가 정규식을 돌리는 줄은 **1,760줄 중 16줄(0.91%)**뿐이다. 전 줄에 강제 호출하면 11.6 ms지만 16줄이면 약 0.1 ms — 기여도가 사실상 0이다.
- 계획에 "실측 이득 근거 없음 — 낮은 우선순위"로 적어 둔 항목이고, 재 보니 근거가 없는 것이 맞았다. **하지 않는다.**

### 남은 것

`evaluate` 20.1 ms 중 나머지는 `ctx.feed()`(명령/구분선 판별)와 FSM 블록 분할이다. 추가 최적화는 3단계 이후에 필요성이 확인되면 다시 측정해 판단한다.

---

## 3단계 — 구조 개선

기능 동작에 영향이 있거나 범위가 넓다. 1·2단계로 이득을 확보한 뒤 착수한다.

### 3-1. 실시간 감시 폴링 payload 델타화

- **대상 파일**: `engine/realtime_monitor.py`(`state`), `api/log_analysis_run_api.py`(`get_realtime_monitor_state`), `web_ui/js/realtime-monitor-panel.js`
- **지금 동작**: `state(tail=160)`은 계산이 1.29 ms(장비 30대)로 싸다. **문제는 전송량**이다 — 폴링 1회당 578 KB, 0.8초 주기로 **723 KB/s**가 pywebview 브릿지를 통과한다(장비 12대 303 KB/s, 4대 118 KB/s). 대부분이 장비별 로그 tail 160줄인데, 그 줄들은 대개 지난 폴링과 동일하다. 프론트엔드는 받은 뒤 9개 render 함수를 전부 돌리고 `innerHTML`을 20곳에서 교체한다 — DOM 전면 teardown/rebuild가 0.8초마다 일어나며, 그 때문에 클릭 고정 강조를 매 렌더마다 `applyRtmCrossHighlight()`로 복원해야 한다는 주석이 코드에 남아 있다(`realtime-monitor-panel.js:305` 부근).
- **변경**: 두 갈래를 함께 해야 의미가 있다.
  1. **서버**: 로그 라인에 장비별 단조증가 `seq`를 붙인다. 클라이언트가 장비별 마지막 `seq`를 보내면 그 이후 줄만 반환한다. 버퍼는 `deque(maxlen=400)`이라 오래된 줄이 버려지므로, 클라이언트 `seq`가 버퍼 최소 `seq`보다 낮으면 해당 장비만 full resync한다.
  2. **클라이언트**: 섹션별(devices/lines, analysis, checklist, pinned, filter) 버전 또는 해시를 비교해 **바뀐 패널만** 다시 그린다. 로그 박스는 교체가 아니라 append로 전환한다. 이렇게 하면 강조 복원 로직이 불필요해진다(DOM이 살아남으므로).
- **예상 이득**: payload가 정상 상태에서 수 KB 수준으로 떨어진다(신규 줄만). 장비 30대 기준 723 KB/s → **추정 10~30 KB/s**. DOM 재구축 제거 효과는 측정하지 않았다 — **추정**.
- **리스크**: HIGH — 실시간 감시는 이 앱의 핵심 기능이고, "왼쪽 로그에는 보이는데 체크리스트는 정상" 같은 패널 간 불일치를 피하기 위해 일부러 한 스냅샷으로 묶어 놓은 설계다(`realtime_monitor.py` 상단 주석). 델타화는 그 불변식을 깰 수 있다. 섹션 버전을 **하나의 스냅샷 번호에서 파생**시켜 원자성을 유지해야 한다.
- **공수**: L
- **검증 방법**: 장비 4/12/30대에서 payload와 폴링 주기별 전송량 측정. 실제 SecureCRT 세션으로 경고 발생 → 복구 → 숨김 → 고정 시나리오를 돌려 세 패널이 어긋나지 않는지 육안 확인. `seq` 재동기화 경로를 버퍼 초과로 강제 유발해 확인.
- **선행 조건**: 0-2(payload 측정 하네스)

### 3-2. 반복 파싱 결과 캐시

- **대상 파일**: `api/report_api.py`(`_latest_terminal_logs_by_device`, `_scan_anomalies`), `api/dashboard_api.py`, `api/log_analysis_run_api.py`, `api/terminal_inspection_api.py`
- **지금 동작**: 같은 raw 로그가 한 사용자 흐름에서 4~5회 재파싱된다 — `api/terminal_inspection_api.py:239`(점검 직후 `analyze_text`), `api/dashboard_api.py:89`(대시보드), `api/report_api.py:157`(`_scan_anomalies`), `api/log_analysis_run_api.py:853`(`extract_suspicious_context` 내부에서 다시 `analyze_text`), `engine/log_analysis.py`의 `run_analysis`. 또 `_latest_terminal_logs_by_device()`는 장비별 로그 전문을 읽는데 `report_api.py:145,154,170,287`과 `log_file_browser_api.py:351`에서 각각 호출된다.
- **변경**: `(path, mtime, size)` 키의 프로세스 단위 캐시를 도입해 raw 텍스트와 `analyze_text()` findings를 재사용한다. 파일이 갱신되면 키가 바뀌어 자동 무효화된다. 메모리 상한(예: 장비 30대 × 로그 전문)을 정하고 LRU로 축출한다.
- **예상 이득**: 2번째 이후 파싱이 사실상 0이 된다. 절대값은 로그 크기 × 재파싱 횟수 — 줄당 47.8 us(Linux)이므로 20,000줄 장비 20대면 1회 파싱에 약 19초, 재파싱 4회 제거로 **추정 수십 초 규모**. 다만 이 시나리오는 실측하지 않았다(합성 코퍼스는 12,800줄 1개) — 3-2 착수 전 실제 수집 로그 크기를 먼저 재야 한다.
- **리스크**: MEDIUM — 캐시 무효화 실수 시 낡은 findings를 보여준다. 점검 직후 대시보드가 이전 회차 결과를 보여주는 것은 이 앱에서 심각한 오류다. `mtime` 해상도(일부 파일시스템 1초)로 같은 초에 두 번 쓰인 파일을 놓칠 수 있으므로 `size`를 키에 반드시 포함한다.
- **공수**: M
- **검증 방법**: 점검 실행 → 대시보드 → 보고서 → AI 분석 순서로 돌며 각 화면 수치가 일치하는지 확인. 로그 파일을 외부에서 수정한 뒤 화면이 갱신되는지 확인.
- **선행 조건**: 0-2, 2-1

### 3-3. 워크스페이스 개요 단일 스캔 ✅ 완료

- **대상 파일**: `api/workspace_api.py`, `engine/run_manager.py`
- **실측 이득**: run 5/20/50개에서 6.15 → 3.02 / 27.37 → 13.74 / 50.87 → 26.39 ms — **약 2배**. 워크스페이스 탭은 폴링되므로(작업 중 1초 / 유휴 5초) 반복 비용이다.
- **중복이 두 겹이었다**:
  1. `get_workspace_overview()`가 `get_run_history()` + `get_current_run_status()` + `_active_run()`을 겹쳐 부르며 활성 run을 여러 번 읽었다 → `_workspace_snapshot()`으로 한 번만 훑고 세 메서드가 그것을 읽는다. 공개 시그니처·반환 형태는 그대로(JS가 셋 다 호출한다).
  2. **`list_runs()`가 run마다 `session.json`을 읽어 요약을 만드는데 `load_run()`이 같은 파일을 또 읽었다**(run당 2회) → `RunManager.load_run_metadata()`를 추가해 `metadata.json`만 읽고 session 값은 요약을 재사용.
- **`get_active_run(summaries=...)` 추가**: 진행 중 run이 **없으면 캐시가 채워지지 않아** 호출마다 전체 `session.json`을 훑는다 — 폴링 시 대개 그 상태다. 판정 규칙(RUNNING/PAUSED 중 최신)을 호출부로 복사하지 않기 위해 인자를 받는 쪽을 택했다.
- **리스크**: LOW(조회 전용) / **공수**: M
- **검증**: `tests/test_workspace_overview.py`(10) — 반환 계약(필드 집합), 활성 run 우선순위, run당 `session.json`/`metadata.json`/`health_score.json`을 각각 1회만 읽는지 **파일 읽기 횟수로 직접** 검증. 내부 필드(`_summary`/`_reports`)가 UI 응답에 새지 않는 것도 고정.

### 3-4. `log_storage` 경로 해석 stat 감소 — ❌ 하지 않음(실측 근거)

- `iter_log_dirs()` 실측: run 1개 **0.16 ms**, 3개 0.24, 10개 0.69, 20개 **1.24 ms**.
- 계획이 "절대 절감폭 미측정 — 착수 전 측정 필요"로 남겨둔 항목이었고, 재 보니 최적화할 값이 없다. legacy 폴더 하위 호환 판정이 이 함수에 집중돼 있어 잘못 건드리면 과거 회차 로그가 목록에서 사라지는 MEDIUM 리스크인데, 이득이 1 ms 대다. **하지 않는다.**
- 네트워크 드라이브에 `data/`를 둔 환경에서는 stat 비용이 훨씬 클 수 있다 — 그런 환경이 실제로 생기면 그때 다시 측정한다.

### 3-5. `dashboard_api` 캐시 키 스캔 제거 — ❌ 하지 않음(실측 근거)

- `get_dashboard()`의 비용 구조 실측(장비 8/20/30대):

  | 장비 수 | 경로 스캔 | 로그 읽기 | 파싱(메모 on) | 파싱(메모 off) |
  |---|---|---|---|---|
  | 8 | 0.51 ms | 0.12 ms | 29.2 ms | 73.0 ms |
  | 20 | 0.61 ms | 0.30 ms | 58.4 ms | 177.1 ms |
  | 30 | 0.65 ms | 0.38 ms | 84.0 ms | 276.3 ms |

- 3-5가 없애려는 것은 **경로 스캔 0.65 ms**로 전체의 **0.8%**다. 게다가 `get_dashboard`는 탭 렌더와 새로고침 버튼에서만 호출되고 **폴링되지 않는다**(`dashboard.js`에 타이머 없음).
- TTL 우선 검사로 바꾸면 **점검 직후 최대 30초간 낡은 수치**를 보여준다. 현재 코드는 디렉터리가 바뀌면 키가 달라져 즉시 갱신되므로, 이건 순수한 기능 후퇴다. 0.65 ms를 위해 치를 대가가 아니다. **하지 않는다.**
- 이 표가 보여주는 진짜 항목은 **3-2(반복 파싱)**다. 파싱이 비용의 99%이고, 2단계 메모가 이미 2.5~3.3배 줄였다(73→29, 177→58, 276→84 ms). 남은 것은 같은 로그를 한 흐름에서 4~5회 재파싱하는 구조다.

### 3-6. 타임스탬프 포맷 통일

- **대상 파일**: 34개 `strftime` 호출 지점, `core/utils/datetime.py`
- **지금 동작**: `core/utils/datetime.py`에 `format_iso` / `format_compact` / `format_run_id` / `parse_iso` 헬퍼가 이미 있는데도 34곳이 `strftime`을 직접 호출한다. **성능이 아니라 정확성 문제가 있다**: `engine/log_storage.py`의 `list_run_dirs()`가 run_id **문자열 정렬**로 최신순을 판정하므로, run_id 포맷이 섞이면 "가장 최근 run" 판정이 조용히 틀린다. 최신 run을 잘못 고르면 보고서·대시보드·분석이 전부 엉뚱한 회차를 본다.
- **변경**: 먼저 **run_id를 만드는 경로만** `format_run_id()`로 통일한다(가장 위험한 부분). 나머지 표시용 `strftime`은 점진적으로 옮긴다. 기존 폴더명과의 호환을 위해 정렬을 문자열이 아닌 파싱된 시각 기준으로 바꿀지는 별도 판단이 필요하다 — 기존 데이터를 깨지 않는 쪽을 택한다.
- **예상 이득**: 성능 이득 없음. 최신 run 오판 위험 제거.
- **리스크**: MEDIUM — run_id 생성 규칙 변경은 기존 폴더와의 정렬 관계를 바꿀 수 있다. 기존 포맷을 읽는 능력은 반드시 유지한다.
- **공수**: M
- **검증 방법**: 여러 포맷의 run_id 폴더를 섞어 놓은 픽스처로 `get_latest_run_dir()`이 실제 최신을 고르는지 확인.
- **선행 조건**: 없음

---

## 측정 필요 (근거 부족)

아래는 개선 여지가 있어 보이지만 **실측 근거가 없어 항목으로 승격하지 않았다**. 착수 전 반드시 측정한다.

| 대상 | 측정할 것 | 측정 방법 |
|---|---|---|
| `core/crt_stream_watcher.py` `_tick()` | 0.3초마다 `os.walk` + 파일별 `getmtime` + 미식별 파일 `_resolve_device`(head 4096 B 재읽기)의 실제 비용. CRTlog에 파일이 수십~수백 개 쌓인 환경에서 유의미한지 | CRTlog에 파일 10/100/500개 픽스처를 만들고 `_tick()` 단독 타이밍 |
| `api/customer_profile_api.py` `_fetch_customer_profiles()` | `project_meta.yaml`을 프로젝트마다 두 번 읽고(첫 루프 + 고객사별 두 번째 루프) 두 번째 루프가 고객사 수 × 프로젝트 수인 점. `core/context_cache.py` 캐시가 미스일 때만 발생하므로 실제 빈도가 중요 | 고객사 10 × 프로파일 20 픽스처로 캐시 미스 시 타이밍 |
| ~~`log_storage`·`dashboard_api` 스캔 비용~~ | **측정 완료 → 3-4/3-5 폐기.** `iter_log_dirs` 0.16~1.24 ms, 대시보드 경로 스캔 0.65 ms(전체의 0.8%) | 위 3-4/3-5 항목 참고 |
| 실제 수집 로그 크기 분포 | 3-2의 이득 추정이 여기에 달려 있다. 장비당 로그 줄 수·파일 크기 실제 분포 | 실사용 `data/` 트리에서 raw 로그 통계 수집 |
| pywebview 브릿지 호출 1회 비용 | 1-3, 3-1 이득의 근거 | Windows 실환경에서 no-op API 메서드 호출 왕복 시간 |
| `api/` 내 raw `open()` 26곳 | 5대 매니저 규칙 우회(`ARCHITECTURE.md` Known follow-ups #2). 원자적 쓰기 보장이 빠진 지점이 실제로 데이터 손상 위험인지 | 각 호출부가 쓰기인지 읽기인지 분류. 쓰기만 문제다 |

---

## 하지 않을 것 (Non-goals)

- **정규식 통합 (31개 signature → 단일 alternation)** — 실측 결과 **2.6배 느리다**(264 ms → 705 ms). 키워드 17개 통합도 **2.7배 느리다**(109 ms → 292 ms). 원인: Python `re`는 패턴별 리터럴 접두사 최적화를 하는데 alternation으로 묶으면 그 최적화를 잃고, 문자열의 모든 위치에서 31개 대안을 백트래킹한다. **이 계획에서 가장 중요한 발견이다** — 직관적으로 가장 그럴듯한 최적화가 실제로는 퇴행이다.
- **리터럴 프리필터 게이트** — 순수 Python 루프 게이트는 0.96배(무효), C 레벨 126-토큰 단일 정규식 게이트는 0.39배(퇴행). 코퍼스의 71%가 게이트를 통과해 필터 효과가 없다.
- **`web_ui/index.html`의 script 태그 31개 번들링** — pywebview는 `file://`로 로드하므로 HTTP 왕복이 없다. 번들링 이득의 근거가 없다. **개선 불필요.**
- **`engine/collector.py` 수집 경로** — 이미 최적이다. 장비 단위 `WorkerPool` 병렬화(최대 50), 장비 내 명령은 SSH 세션이 stateful하므로 의도적 순차, 그리고 명령들을 하나의 bulk 텍스트로 묶어 왕복을 1회로 줄였다. 손대지 않는다.
- **`api/log_analysis_run_api.py`(910줄) 파일 분리** — 성능 이득이 없고, 믹스인 조합 순서 의존성(`LogFileBrowserApiMixin`, `SettingsApiMixin`과의 상호 참조)을 깰 위험만 있다. 성능 계획의 범위 밖이다.
- **`ARCHITECTURE.md` Known follow-ups #1/#3 (run 생성 경로 통합, 미연결 매니저 메서드 13개)** — 구조 부채지만 성능과 무관하다. 이 계획에서 다루지 않는다.
- **`core/storage_service.py` ⇄ `engine/run_manager.py` 순환 import 해소** — 지연 import로 이미 동작하고, 시작 비용에 기여하지 않는다(1-1의 무거운 의존성과 무관). 성능 사유로는 착수하지 않는다.
- **전면 리팩터링** — 동작 중인 실무 도구다. 각 항목은 독립 커밋으로 되돌릴 수 있어야 한다.

---

## 수용 기준

숫자로 판정한다. 모두 0-2 벤치마크 하네스의 고정 시드 코퍼스로 측정한다.

1. **0단계** ✅ — 테스트 69개가 현재 코드에서 전부 통과한다(`python -m pytest tests/ -q`). 하네스 측정 편차 p75 3.5%(≤5%). 기준선 조작 시 퇴행 3건을 모두 포착하고 exit 1을 낸다. 8회 연속 `--check`에서 7회 정상(1회는 컨테이너 경합에 의한 오탐 — 위 한계 참고).
2. **1-1** ✅(부분) — 16개 믹스인이 세 패키지 **미설치 상태에서 전부 import** 됨을 확인했다. 남은 확인: Windows 에서 `python -X importtime main.py` 로 창이 뜨기까지의 시간이 **1.0초 이상** 단축되는지.
3. **1-2** ✅ — `AppPaths.crt_log_root()` 반복 호출 **0.22 us/call**(기준 1 us 이하, 변경 전 9.8 us). 남은 확인: 앱 실행 중 `CRTlog` 를 지운 뒤에도 실시간 감시가 동작하는지(수동 확인 필요 — 쓰기 경로가 각자 `makedirs` 하는 것은 코드로 확인했다).
4. **2-1/2-2** ✅ — `match_signature_memo_speedup_run` **3.11배**(기준 3.0), `find_keyword_memo_speedup_run` **3.47배**(기준 3.3). `analyze_text` 누적 **2.72배**(70.2 → 25.8 ms). 장비 20대 회차에서 메모 on/off findings **완전 일치**. 메모 상한 준수 확인. 남은 확인: **실제 수집 로그**로 on/off 일치를 한 번 더 대조(합성 코퍼스로만 확인했다).
5. **2-3** — 하지 않기로 결정(실측 기여도 0.91% 줄, 약 0.1 ms). 별도 수용 기준 없음.
6. **3-1**: 장비 30대 정상 상태에서 폴링 payload가 **50 KB/s 이하**(현재 723 KB/s). 경고 발생→복구→숨김→고정 시나리오에서 세 패널이 어긋나지 않는다. `seq` 재동기화가 버퍼 초과 시 정상 동작한다.
7. **3-2**: 점검 → 대시보드 → 보고서 → AI 분석 흐름에서 raw 로그 `analyze_text()` 호출이 장비당 **1회**로 줄어든다. 네 화면의 수치가 일치한다.
8. **전 단계 공통**: 골든 테스트가 모든 커밋에서 통과한다. 어느 단계에서 멈춰도 그 시점까지의 이득이 남고 앱이 정상 동작한다.
