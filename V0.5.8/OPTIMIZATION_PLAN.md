# AutoCheck V0.5.8 최적화 계획

## 요약

앱의 최대 CPU 소비처는 **로그 판정 엔진**(`engine/log_rule_engine.py`)이고, 그중 `match_signature()`가 `evaluate()` 시간의 약 56%다. 다만 이 병목의 해법으로 흔히 떠오르는 **정규식 통합(31개 → 단일 alternation)은 실측 결과 2.6배 느려서 폐기**했다. 실제로 성립하는 해법은 **줄 단위 메모화**이며(장비 20대 회차에서 3.9~4.3배, 결과 동일), 31개 signature 전부가 `scope=None`이라 순수 함수임을 확인해 안전성이 확보됐다. 단, 메모는 **회차 전체가 공유**해야 한다 — 파일 단위 메모는 1.05배로 사실상 무의미하다(0단계 하네스로 확인).

두 번째로 큰 것은 **시작 비용 약 1.27초**로, 실제로는 쓰지도 않는 pandas/paramiko/openpyxl을 eager import하기 때문이다. 세 번째는 **실시간 감시 폴링의 payload**(장비 30대에서 723 KB/s)인데, 계산 자체는 1.3ms로 싸고 문제는 전송량과 DOM 전면 재구축이다.

테스트가 0개이므로 **0단계(안전망)를 먼저** 만들고, 그다음 무위험 항목(시작 비용·경로 캐시) → 핫패스(메모화) → 구조 개선 순으로 진행한다.

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
| 테스트 수 | 0개 → **140개 통과**(0·1단계 완료) | `python -m pytest tests/ -q` | 이번 세션 |

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

## 2단계 — 핫패스 최적화

`evaluate()`가 앱 최대 CPU 소비처다. **0단계 완료 후에만** 착수한다.

### 2-1. `match_signature()` / `find_keyword()` 줄 단위 메모화 ★최우선

- **대상 파일**: `engine/log_rule_engine.py` (`SignatureMatcher.match_signature`, `RuleEngine.find_keyword`)
- **지금 동작**: 줄마다 signature 31개를 순차 루프하고(191 ms/9,600줄), signature가 안 걸리면 다시 keyword 17개를 순차 루프한다(103 ms/9,600줄). 실제 네트워크 로그는 중복률이 높다 — 인터페이스 표 행, 카운터 0 행, 설정 원문이 장비마다 반복된다. 합성 현실 코퍼스에서 `evaluate()`에 도달하는 1,312줄 중 **고유 줄은 417개(중복 68%)**였다.
- **변경**: 두 함수를 줄 문자열 키로 메모화한다. **안전성 근거**: 31개 signature 전부 `scope=None`임을 실측 확인했다(`0/31`). `match_signature()`의 유일한 ctx 의존은 `if scope and not scope.search(ctx.command)`이므로 scope가 없으면 ctx를 전혀 읽지 않는다 — 줄만의 순수 함수다. `find_keyword()`는 애초에 ctx를 받지 않는다.
  ```python
  class SignatureMatcher:
      def __init__(self, rules):
          ...
          self._memo = {}
          # scope 보유 서명이 하나라도 있으면 메모를 끈다 — 그때는 ctx 의존이 생긴다
          self._memo_safe = all(sc is None for _rx, sc, _e in self.signatures)

      def match_signature(self, line, ctx):
          if not self._memo_safe:
              return self._match_uncached(line, ctx)
          hit = self._memo.get(line, _MISS)
          if hit is _MISS:
              hit = self._match_uncached(line, ctx)
              if len(self._memo) < _MEMO_MAX:      # 상한으로 메모리 폭주 방지
                  self._memo[line] = hit
          return hit
  ```
  메모는 `RuleEngine` 인스턴스에 소속시킨다 — `get_engine(reload=True)`가 새 엔진을 만들므로 규칙 변경 시 자동 무효화된다. 상한(`_MEMO_MAX`, 예: 50,000 엔트리)에 도달하면 더 담지 않는다(캐시 자체를 비우지는 않는다 — 고유 줄이 많은 로그에서 진동을 피하기 위함).
  - `self._memo_safe` 가드가 핵심이다. 앞으로 누군가 `log_rules.json`에 `scope`를 가진 signature를 추가하면 메모가 자동으로 꺼지고 정확성이 유지된다. 0-1의 `test_no_signature_declares_scope`가 그때 실패해 알려 준다.
  - **메모는 반드시 회차 전체를 살아남아야 한다.** 파일마다 새로 만들면 1.05배로 이득이 사라진다(위 곡선 표). `RuleEngine`이 프로세스 싱글턴이므로 인스턴스 속성으로 두면 자동 충족되지만, 파일 단위로 엔진을 새로 만들거나 메모를 비우는 코드를 넣으면 이 항목이 무의미해진다.
- **예상 이득**(0-2 하네스 실측, Linux): 회차 공유 기준 **장비 4대 2.5배 / 8대 3.2배 / 20대 3.9배**(`match_signature`), `find_keyword`는 2.6 / 3.5 / 4.3배. 장비 수가 많을수록 커지고 히트율 77.7%에서 포화하는 경향이다. `evaluate()` 전체로는 `suppressor.check` 12.6 ms가 그대로 남으므로 2배 내외가 현실적이다.
  - 이전 세션의 6.9배/9.0배 주장은 **워밍된 메모를 평균에 섞어 재서 나온 과대평가**였다. 0-2 하네스가 파일 단위와 회차 단위를 분리해 재도록 만든 이유가 이것이다.
- **리스크**: MEDIUM — 판정 로직 경로에 캐시를 넣는 변경이다. `_memo_safe` 가드와 0-1 골든 테스트가 함께 있어야 한다. 메모리는 고유 줄 수 × 줄 길이이므로 상한이 필수다.
- **공수**: M
- **검증 방법**: 0-1 골든 테스트 52개 전부 통과(특히 `test_full_pipeline_snapshot`) + 메모 on/off로 `analyze_text()` 결과가 완전히 동일함을 합성 코퍼스와 실제 수집 로그 양쪽에서 확인 + `python -m tools.bench_log_analysis --check`로 `match_signature_memo_speedup_run` 개선 확인.
- **선행 조건**: **0-1, 0-2 (완료)**

### 2-2. `suppressor.check()` 이중 호출 제거

- **대상 파일**: `engine/log_rule_engine.py` (`RuleEngine.evaluate`)
- **지금 동작**: `evaluate()`가 `suppressor.check(line, None, ctx, include_config_scope=False)`로 한 번 부르고, 키워드가 걸린 줄에서는 `suppressor.check(line, keyword, ctx)`로 다시 부른다. 두 번째 호출은 `benign_phrases` 루프(7개 lowercase substring), `is_legend`, `_FEATURE_ROW_RE`, 그리고 suppression 패턴 10개 루프를 **모두 재실행**한다. 첫 호출과 달라지는 부분은 `counter_value(line, keyword)`와 config-scope 판정 두 가지뿐이다.
- **변경**: `check()`를 구조 억제(키워드 무관)와 키워드 결합 억제로 쪼갠다. `evaluate()`는 구조 억제를 1회만 실행하고, 키워드가 걸린 뒤에는 `counter_value` + config-scope만 추가 검사한다.
- **예상 이득**: suppressor 59 ms 중 두 번째 호출분 절감. **키워드 매치 줄의 비율에 비례하므로 코퍼스 의존적이다** — 이번 합성 코퍼스에서는 키워드 매치가 적어 절감폭이 작았다. 0-2 하네스로 실제 수집 로그에서 먼저 비율을 재고 착수 여부를 정한다.
- **리스크**: MEDIUM — 억제는 오탐 억제의 핵심이라 순서/조건을 잘못 쪼개면 오탐이 되살아난다. 골든 테스트의 오탐 케이스 4개가 이 항목의 안전망이다.
- **공수**: M
- **검증 방법**: 0-1 골든 테스트 + 실제 수집 로그로 before/after findings 완전 일치 확인.
- **선행 조건**: **0-1**, 2-1(같은 함수 주변을 건드리므로 순서를 지킨다)

### 2-3. `Suppressor.counter_value()` 정규식 사전 컴파일

- **대상 파일**: `engine/log_rule_engine.py` (`Suppressor.counter_value`)
- **지금 동작**: 호출마다 `self._COUNTER_BEFORE % kw` / `_COUNTER_AFTER % kw`로 정규식 문자열을 포맷하고 `re.finditer`에 넘긴다. `re` 내부 캐시가 있어 재컴파일은 피하지만 문자열 포맷과 캐시 조회는 매번 발생한다.
- **변경**: `counter_columns`가 고정 집합이므로 `__init__`에서 컬럼별로 두 패턴을 미리 컴파일해 dict에 담는다(15개 컬럼 × 2 = 30개).
- **예상 이득**: `counter_row`가 2 ms/9,600줄로 이미 미미하고 `counter_value`도 같은 규모다. **실측 이득 근거 없음 — 낮은 우선순위.** 2-2를 하면서 같은 클래스를 건드릴 때 함께 정리하는 정도로 취급한다.
- **리스크**: LOW
- **공수**: S
- **검증 방법**: 0-1 골든 테스트.
- **선행 조건**: 없음

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

### 3-3. 워크스페이스 개요 N+1 제거

- **대상 파일**: `api/workspace_api.py`(`get_workspace_overview`, `get_run_history`, `get_current_run_status`)
- **지금 동작**: `get_workspace_overview()`가 `get_run_history()`와 `get_current_run_status()`를 각각 호출하고, 둘 다 내부에서 `_active_customer_profile()`과 `_active_run()`을 중복 수행한다(`_active_run()`은 `prm.list_runs()`를 다시 호출). `get_run_history()`는 run마다 `load_run()`(session.json + metadata.json) + `health_score.json` + `list_reports()`를 읽는 전형적 N+1이다.
- **변경**: 개요 1회에 필요한 데이터를 한 번의 스캔으로 모으는 내부 함수를 만들고 세 공개 메서드가 그것을 공유한다. run 목록·활성 run 해석을 요청 범위에서 1회로 줄인다.
- **예상 이득**: run 수에 선형. **run 개수별 실측이 없다 — 착수 전 측정 필요.**
- **리스크**: LOW — 조회 전용 경로다.
- **공수**: M
- **검증 방법**: 워크스페이스 탭의 Run History / Current Run Status 표시가 이전과 동일한지 확인. run 20개 이상 환경에서 렌더 시간 측정.
- **선행 조건**: 3-5(측정)

### 3-4. `log_storage` 경로 해석 stat 감소

- **대상 파일**: `engine/log_storage.py`(`iter_log_dirs`, `list_run_dirs`, `_run_paths`, `_run_kind_dir`)
- **지금 동작**: `_run_paths()`가 run마다 4개 kind에 대해 `_run_kind_dir()`을 호출하고, 각각 canonical + legacy 후보를 `is_dir()`로 확인한다 — run당 최대 약 8회 stat. `iter_log_dirs()`는 `list_run_dirs()`를 다시 호출해 이 작업을 반복한다.
- **변경**: run 디렉터리를 한 번 `scandir`하여 존재하는 하위 폴더 집합을 얻고 그것으로 canonical/legacy를 판정한다. `iter_log_dirs()`는 `list_run_dirs()` 결과를 인자로 받도록 해 중복 호출을 없앤다.
- **예상 이득**: run당 stat 약 8회 → 1회 `scandir`. **절대 절감폭 미측정 — 착수 전 측정 필요.** 로컬 SSD에서는 무의미할 수 있고, 네트워크 드라이브에 `data/`를 둔 환경에서 의미가 커진다.
- **리스크**: MEDIUM — legacy 폴더 하위 호환 판정이 이 함수에 집중돼 있다. 잘못 건드리면 과거 회차 로그가 목록에서 사라진다("한쪽에는 보이는데 다른 쪽에서는 안 지워지는 유령 로그"를 막기 위해 일부러 단일 함수로 모은 곳이다).
- **공수**: M
- **검증 방법**: legacy 폴더 이름(`00_orignal_log`, `01_problem_log`, `cache/original_log` 등)을 가진 픽스처로 `iter_log_dirs()` 결과가 before/after 동일함을 확인.
- **선행 조건**: 3-5(측정)

### 3-5. `dashboard_api` 캐시 키 스캔 제거 + 측정

- **대상 파일**: `api/dashboard_api.py`(`_scan_latest_logs`)
- **지금 동작**: `_scan_cache`가 TTL 30초로 결과를 재사용하지만, **캐시 키를 만들기 위해** 매번 `_latest_terminal_log_paths_by_device()`를 호출해 디렉터리를 전수 스캔한다(각 파일 `getmtime`). 즉 캐시 히트여도 디렉터리 스캔 비용은 그대로 낸다.
- **변경**: TTL 내에서는 키 계산 자체를 건너뛰고 저장된 값을 즉시 반환한다. 정확한 무효화가 필요하면 run 폴더 mtime 1회 확인으로 대체한다.
- **예상 이득**: 캐시 히트 시 디렉터리 스캔 완전 제거. **파일 수별 실측 없음 — 이 항목에 3-3/3-4의 측정도 함께 묶어 수행한다.**
- **리스크**: LOW — 최대 30초간 낡은 수치를 보여줄 수 있으나 그것이 이미 현재 TTL 설계의 의도다.
- **공수**: S
- **검증 방법**: 점검 직후 30초 내 대시보드가 새 수치를 반영하는지 확인(반영돼야 한다면 TTL 설계 자체를 재검토).
- **선행 조건**: 없음

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
4. **2-1**: 기준선 코퍼스(장비 8대, 중복률 73.2%)에서 `match_signature_memo_speedup_run` **3.0배 이상**, `find_keyword_memo_speedup_run` **3.3배 이상**. `analyze_text()` findings가 메모 on/off에서 **완전히 동일**(합성 코퍼스 + 실제 수집 로그 양쪽) — 0-1 스냅샷 테스트가 이것을 판정한다. 메모 엔트리가 상한을 넘지 않는다. 파일 단위 이득(1.05배)이 아니라 회차 공유 이득이 나오는지 반드시 확인한다.
5. **2-2**: 실제 수집 로그에서 findings before/after 완전 일치. `suppressor.check` 호출 횟수가 키워드 매치 줄에서 2회 → 1회.
6. **3-1**: 장비 30대 정상 상태에서 폴링 payload가 **50 KB/s 이하**(현재 723 KB/s). 경고 발생→복구→숨김→고정 시나리오에서 세 패널이 어긋나지 않는다. `seq` 재동기화가 버퍼 초과 시 정상 동작한다.
7. **3-2**: 점검 → 대시보드 → 보고서 → AI 분석 흐름에서 raw 로그 `analyze_text()` 호출이 장비당 **1회**로 줄어든다. 네 화면의 수치가 일치한다.
8. **전 단계 공통**: 골든 테스트가 모든 커밋에서 통과한다. 어느 단계에서 멈춰도 그 시점까지의 이득이 남고 앱이 정상 동작한다.
