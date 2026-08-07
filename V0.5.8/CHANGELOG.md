# CHANGELOG

버전 규칙: 수정할 때마다 마지막 자리 1씩 증가. 인사평가 KPI 계획상 v1.0.0은 8월(v1.0 단계,
"핵심 기능 + 전체 완성") 전까지 올리지 않음 — 지금은 전부 0.0.x 범위.

**번호 체계 통일(v0.0.025부터)** — 그동안 폴더명(`V0/lab_grader_V0.0.025`)과 `VERSION` 파일은
3자리(`0.0.025`)를, CHANGELOG는 2자리(`v0.0.15`)를 써서 같은 릴리스가 두 개의 번호로 불렸다.
이제 **3자리(`0.0.0NN`)로 통일**하고 폴더명 = `VERSION` = CHANGELOG 제목이 항상 같은 값을 쓴다.
UI 좌하단에 표시되는 값도 `VERSION` 파일을 그대로 읽은 것이라 자동으로 일치한다.
아래 v0.0.15 이하의 과거 항목은 당시 기록이라 번호를 손대지 않았다 (v0.0.15 = 폴더 V0.0.024).

## v0.5.9 (예정 — 폴더/VERSION 은 아직 0.5.8이라 릴리스 시 함께 올릴 것) — 정기점검 보고서 오탐 정리: '점검 불가/대상 아님'을 '비정상'과 분리

### 네트워크 구성도 자동 생성 (신규 탭)

장비 목록과 수집된 점검 로그에서 **물리 구성도를 자동으로 그린다.** 새로 수집하는 것은 없다 —
정기점검이 이미 모으는 네 가지 출력만으로 구성이 복원된다(실제 워크스페이스에서 확인):

| 출력 | 얻는 것 |
|---|---|
| `show lldp neighbors` | 무엇이 무엇의 어느 포트에 붙어 있는가 (연결의 유일한 근거) |
| `show interfaces status` | 링크 상태 + Port-Channel 소속(`in Po2048`) |
| `show interfaces description` | 사람이 붙인 링크 설명(`core_agg_mlag`) |
| `show mlag` | peer-link Po → 이중화 쌍 |

실측: 랩 7대에서 노드 7 · 링크 12(Po 묶음 6 + 하향 6) · MLAG 쌍 2 · 경고 0.

- **표준 네트워크 다이어그램 기호·규칙을 따른다** (`engine/topology_svg.py`)
  - Cisco 의 실제 아이콘 아트워크는 저작권/상표 대상이라 복제하지 않고 **같은 의미의 관례적
    기하 도형**을 직접 그린다: L2 스위치(나란한 화살표 4개) / L3 스위치(+ 라우터 화살표) /
    라우터(원 + 방사형) / 방화벽(벽돌) / 미등록(점선 + `?`).
  - 연결선: 단일 물리 링크 실선 · Po 묶음 굵은 선 + 브래킷 + `Po2048 ×2` · MLAG 쌍은 점선 상자 ·
    DOWN 은 빨간 선 + **✕ 마커**(색만으로 구별하면 색약·흑백 인쇄에서 사라진다) · 묶음 일부
    DOWN 은 주황 + 반쪽 원(이중화가 이미 깎였다는 뜻) · 판정 불가는 회색.
  - 계층형 배치(Core 위 → Access 아래), 이중화 쌍은 나란히, **범례를 항상 그린다.**
  - 같은 노드에서 같은 방향으로 나가는 링크를 기호 가장자리에 **부채꼴로 펼친다** — 한 점에서
    모두 나가면 선이 겹쳐 몇 개인지 보이지 않고 포트 라벨이 같은 자리에 쌓인다(이중화 구성에서는
    거의 항상 그렇게 된다). 중점이 겹치는 대각선들의 라벨도 선 위에서 어긋나게 놓는다.

- **판정 다섯 가지** (`engine/topology_builder.py`)
  - 양방향 중복 제거. 한쪽에서만 보이는 링크는 **버리지 않고** 경고로 남긴다 — 조용히 지우면
    단선을 놓친다.
  - Po 묶음. 하나의 Po 가 서로 다른 이웃으로 갈라지는 것은 정상이다(MLAG 가 그 모양이다).
  - MLAG 쌍 = peer-link Po 에 속한 포트의 LLDP 이웃.
  - **미등록 이웃**(LLDP 에만 보이는 장비)을 점선 노드로 남긴다 — 문서화되지 않은 연결을
    찾아내는 것이 이 화면의 점검 가치다.
  - 계층은 role 필드 → 이름 키워드 → **실패하면 '미분류'로 남기고 알린다.** 그래프 구조로
    추정하지 않는다: collapsed core/agg 설계에서는 차수만으로 Core 와 Agg 를 가릴 수 없고
    (실측 랩에서 Agg 의 이웃이 Core 보다 많다), 틀린 계층을 자동으로 그리면 사용자는 그림을
    믿을 수 없으면서 무엇이 틀렸는지도 알 수 없다.

- **직접 배치**(`engine/topology_layout.py`) — 노드를 끌어 옮기면 프로파일별로 저장된다
  (`config/topology_layout.yaml`). 계층 자동 판정이 틀렸을 때의 탈출구이고, 실제 인벤토리의
  `role` 이 전부 비어 있어서(확인함) 이 경로가 반드시 필요하다. 자동 배치는 결정적이다 —
  힘기반 배치는 예쁘지만 매번 다른 그림을 줘서 회차를 비교할 수 없다.

- **실시간 감시와 연동**(`api/topology_api.py`) — 링크 상태의 단일 출처는
  `BaselineDiffEngine.open_conditions()` 다. 거기서 `component_id` 가 정규화된 인터페이스명이라
  링크의 한쪽 끝을 이름 변환 없이 찾는다. 세션 syslog 로 잡힌 DOWN 과 상태 폴링으로 잡힌 DOWN 이
  이미 같은 축으로 합쳐져 있어 출처를 구분할 필요가 없다. 여기서 새로 판정하지 않는다 —
  두 곳에서 판정하면 '구성도는 빨간데 실시간 감시는 정상'인 화면이 나온다.
  상태를 관측할 수 없는 장비의 링크는 초록이 아니라 **회색(판정 불가)** 이다.

- **SVG 내보내기** — 화면과 파일이 **같은 렌더러**를 쓴다(두 개로 나누면 '화면과 파일이 다르다'가
  된다). 파일은 앱 밖에서 열리므로 색만 인라인으로 심는다(`standalone=True`).

- **파서 보강**
  - `parsers/show_lldp_neighbors.py` 신설. 열 위치가 아니라 '마지막 열이 숫자(TTL)'라는 구조로
    읽는다 — 칼럼 정렬에 의존하면 벤더/장비마다 깨진다. `Last table change time` 같은 요약 줄을
    이웃으로 읽지 않는 것이 이 파서의 일 대부분이다.
  - `parsers/show_interfaces_status.py` — `parse_port_channel_membership()`(`in Po2048`),
    `parse_descriptions()` 추가.
  - `parsers/show_inventory_mlag_vrrp.py` — `parse_mlag()` 이 config 절(`peer-link`,
    `peer-address`, `domain-id`)도 읽는다. 키가 늘어난 것뿐이라 기존 소비자
    (`engine/comparators/mlag.py`, `engine/state_poller.py`)에는 영향이 없다.

- **UI** — 사이드바 '네트워크 구성도' 탭, 휠 확대·축소, 빈 곳 드래그로 이동, 노드 드래그로 배치,
  노드/링크 클릭 시 우측 상세(연결 목록·Po 멤버·미해결 경고), 그림이 비어 보이는 이유를 장비별로
  보여주는 '진단' 모달. `AutoCheck.spec` 은 고칠 필요 없다(`web_ui` 폴더 전체 번들 +
  `collect_submodules` 가 `engine`/`parsers`/`api` 를 이미 포함).

- **회귀 테스트** `tests/test_topology.py`(34건) — 픽스처는 실제 워크스페이스 출력을 그대로 쓴다.
  상상해서 만든 벤더 출력으로 테스트하면 현장에서 한 줄도 못 읽는 파서가 통과한다(이 프로젝트의
  기존 BGP/OSPF 파서가 실제로 그 상태였다).

### '점검 로그' 삭제 버튼이 파일을 지우지 않던 문제

보고된 증상: 삭제 버튼을 눌러도 파일이 지워지지 않고 **오류 메시지도 뜨지 않았다.**

- **원인 — `api/log_file_browser_api.py` 의 정의되지 않은 변수 하나**
  - `_derived_output_paths()` 가 마스킹 결과 이름을 만들 때 존재하지 않는 `body` 를 참조했다
    (`f"{body}_masked.txt"`). 이 줄은 활성 프로파일에 `masked/` 폴더가 있을 때만 도달하므로,
    마스킹을 한 번이라도 돌린 프로파일에서만 재현됐다.
  - `delete_log_files()` 는 **지울 목록을 만드는 도중** 이 함수를 부른다. 그래서 NameError 가
    `os.remove` 가 한 번도 불리기 전에 터졌고 — 즉 부분 삭제도 아니고 전혀 삭제되지 않았다.
  - 확장자를 뗀 원본 파일명으로 고쳤다(`engine/log_masking.py` 의 저장 규칙과 같은 근거).
  - 파생 결과 경로 계산을 try 로 감쌌다. **부수적인 정리 작업이 본래 요청을 삼켜서는 안 된다** —
    파생 경로를 못 구해도 사용자가 지우라고 한 파일은 지운다.

- **왜 조용했나 — `web_ui/js/core.js` 의 `call()` 이 예외를 흘려보냈다**
  - 파이썬에서 예외가 나면 pywebview 가 프라미스를 reject 하는데, 호출부는 대개
    `const result = await call(...)` 한 줄이라 그 자리에서 함수가 중단됐다. 목록 갱신도,
    실패 alert 도 실행되지 않아 '버튼이 먹지 않는다'로만 보였다.
  - 이제 `call()` 이 API 예외를 잡아 콘솔과 토스트로 드러내고 `null` 을 돌려준다. 호출부는
    이미 `|| {deleted: [], errors: {}}` 같은 방어를 갖고 있어 중단되지 않는다. 이 수정으로
    앞으로 어떤 API 예외든 화면에 보인다 — 이 버그가 오래 눈에 띄지 않은 이유가 그것이었다.

- **전수 확인** — pyflakes 로 `api/ engine/ core/ report/ parsers/ plugins/ rule_engine/
  pipeline/ ai_analysis/ alarm/ tools/ main.py` 를 훑어 같은 종류(undefined name)의 버그가
  이 하나뿐임을 확인했다(현재 잔여 0건).

- **회귀 테스트** `tests/test_log_file_delete.py`(9건) — 실제 삭제·파생 결과 동반 삭제·
  파생 경로 계산 실패에도 본래 삭제는 되는지·경로 경계(프로파일 밖/비 txt)·이미 없는 파일.

### 실시간 감시 정확성 전면 수정 — '엉뚱한 것을 보고 있던' 문제

증상 진단은 코드가 아니라 **실제 워크스페이스 데이터**에서 나왔다. 5일간 사용한 프로파일
3개의 `realtime_state.json` 에 남은 경고가 **총 1건**이었고, 그 1건이 오탐이었다.

```
Access1 CRITICAL DESTRUCTIVE_COMMAND  "위험 명령 실행 감지: Reload Cause:"
```

`Reload Cause:` 는 `show reload cause` **출력의 머리글**이며 CRTlog(수동 SecureCRT 세션 로그)
60여 개에는 한 번도 등장하지 않는 문자열이다 — 점검 결과 폴더에만 있다. 여기서 세 층의
원인이 드러났다.

- **감시 대상 폴더가 점검이 끝날 때마다 바뀌고 되돌아오지 않았다** (`api/log_analysis_run_api.py`)
  - `refresh_realtime_baseline_after_inspection()` 이 `watcher.set_watch_dir(paths["original"])`
    로 감시 대상을 `runs/<run>/raw` 로 옮겼고, CRTlog 로 되돌리는 코드가 없었다. 결과:
    작업자의 SecureCRT 세션 감시가 앱 재시작까지 영구 정지 + 점검 출력이 '지금 들어온 입력'
    으로 재판정(위 오탐의 경로). 화면은 계속 CRTlog 를 감시 중이라고 표시해 원인을 감췄다.
  - 그 3줄을 제거했다. 점검은 별도 SSH 세션이라 CRT 세션 로그에 아무것도 쓰지 않으므로,
    갈아끼울 것은 Baseline 스냅샷뿐이다(주석은 원래 그렇게 쓰여 있었다).
  - 함께 있던 `engine.reset_context()` 도 뺐다 — StateTracker 를 비우므로 점검이 끝나는 순간
    열려 있던 장애가 '취소 불가'가 되고, 나중에 `no shutdown` 을 쳐도 해제되지 않았다.
  - 2차 방어: `core/crt_stream_watcher.py` 의 순회가 점검 결과 파일명
    (`{stamp}_raw_{device}.txt`)을 **이름으로** 건너뛴다. 폴더를 잘못 지정하는 어떤 경로에도 걸린다.
  - `watch_dir` 보고를 상수에서 실측값으로 바꿨다(`_realtime_watch_dir()`) — 불일치는 화면이
    드러내야 한다. 진단 모달도 감시 스레드와 **같은 순회 함수**(`iter_session_log_files()`)를 쓴다.
    예전에는 진단만 최상위를 `listdir` 해서, 하위 폴더 로깅 환경에서 '감시는 추적 중인데
    진단에는 없다'가 됐다. `max_depth` 계산의 off-by-one(1단계 지정인데 2단계까지 내려감)도 고쳤다.

- **'작업자 입력'과 '장비 출력'을 구분하지 않았다** (`engine/baseline_diff_engine.py`)
  - 모든 정규식이 `^\s*` 로 시작해서 들여쓰인 출력도 명령으로 읽혔다. 실제 로그에서 확인된 것:
    `Reload Cause:`, `?` 도움말의 `  reload   Reboot the system`(파일당 5줄),
    running-config 의 `   no shutdown`(장비 7대 전부 2줄) · `vlan N`(4~8줄) · `interface EthernetN`(18~42줄).
  - **오탐보다 심각한 것은 취소 방향이었다.** 작업자가 `no vlan 100` 으로 CRITICAL 을 띄운 뒤
    `show running-config` 를 한 번 보면, 출력 안의 `vlan 100` / `no shutdown` 이 복구 이벤트로
    읽혀 진짜 경고가 '복구됨'으로 지워졌다 — 설정을 들여다보는 것이 감시를 무력화하는 경로.
  - `classify_line()` 신설: 프롬프트가 붙은 줄만 COMMAND, 나머지는 OUTPUT(선행 공백 불허).
    설정변경·파괴명령 패턴은 COMMAND 에만, syslog 패턴은 OUTPUT 에만 적용한다. 판정 못 한 줄은
    OUTPUT 으로 취급한다(보수적 — 놓치는 것은 생기지만 없는 일을 만들지 않는다).
  - config 문맥을 **프롬프트 괄호**에서 읽는다(`config-if-Et1` → Ethernet1). `interface X` 줄을
    따라다니는 것보다 안전하고, 프롬프트가 특권 모드로 돌아오면 낡은 문맥을 즉시 버린다.
  - `show mlag` 같은 조회 출력에서는 **복구 이벤트를 받지 않는다**(syslog 모양일 때만 허용).
    비대칭인 이유: 틀린 DOWN 은 보이는 잡음이지만 틀린 UP 은 진짜 장애를 조용히 지운다.
  - `show logging` 계열 출력에서 나온 경고는 `history` 로 표시한다 — 며칠 전 이벤트를 그대로
    다시 뿌리는 출력이다.
  - 실측: 점검 로그 1,528줄 투입 → 경고 0건·취소 0건(열린 조건 유지). 실제 CRT 세션 로그
    2,977줄 → 오탐 0건, 작업자가 실제로 친 `no vlan …`/`no interface …` **14건 검출**.

- **볼 수 없는 것을 '정상'으로 표시했다** (`engine/realtime_monitor.py`, `web_ui/`)
  - CRT 세션 로그 전체에 syslog 줄이 **0건**이었다(`terminal monitor` 미설정). 링크/인접/
    STP·MLAG 판정은 syslog 에서만 나오므로 체크리스트 7항목 중 3개가 구조적으로 영원히
    '변경 없음(정상)'이었다 — 그 화면을 근거로 점검을 마무리한다.
  - `CHECK_ITEMS` 에 판정에 필요한 입력원(sources)을 붙였다. 그 입력원이 한 번도 관측되지
    않은 항목은 `pending`(정상)이 아니라 `unknown`(판정 불가 — syslog 미수신)으로 **읽는 시점에**
    파생시킨다(syslog 가 들어오기 시작하면 곧바로 풀린다). 관측된 판정(fail/warn)은 덮지 않는다.
  - `BaselineDiffEngine.observations()` 가 장비별 commands/syslog/output 을 센다. 스냅샷에
    저장돼 재실행 후에도 유지된다.
  - 요약 문구를 셋으로 갈랐다: 진짜 이상 없음 / 명령은 보이는데 syslog 없음(판정 불가) /
    아무 입력 없음. 앞의 둘은 초록색이 아니다 — `verdict: "unknown"` + 중립색(`.rtm-unknown`),
    상단 상태줄에도 '입력 없음' · 'syslog 없음 N대' 배지와 툴팁을 붙였다.
    체크리스트 라벨 `기준없음` → `판정불가`(이유는 detail 이 밝힌다).
  - STP mnemonic 수정: `STP-\d-` 는 Arista/Cisco 어느 쪽에도 매치되지 않았다(실제 mnemonic 은
    `%SPANTREE-n-…`) — STP 로그를 통째로 놓치고 있었다.

- **링크 복구가 영원히 인식되지 않았다** (`engine/baseline_diff_engine.py`)
  - `%LINEPROTO-5-UPDOWN` 은 up 이든 down 이든 mnemonic 에 'down' 을 품고 있는데, 방향 판정이
    줄 전체를 봤다. 그래서 `changed state to up` 이 DOWN 으로 읽혀 **LINK_UP 이 한 번도 발행되지
    않았고**, 작업자가 링크를 되살려도 CRITICAL 이 화면에 남았다. BGP `Up` 도 같았다.
  - `_SYSLOG_PATTERNS` 에 상태 캡처 그룹을 명시하고, 그 값으로 방향을 정한다. 값으로 판정이
    안 되는 경우(`administratively down`)만 mnemonic 을 지운 줄로 되짚는다.

- **플랩이 '복구됨'으로 뒤집혔다** (`engine/baseline_diff_engine.py`)
  - dedupe 창(10초) 안에서 down → up → down 이 벌어지면 두 번째 down 이 '중복'으로 버려져
    조건이 다시 열리지 않았다. 상태가 전이된 구성요소의 억제 기록을 버려 다음 전이가 반드시
    통과하게 했고, 줄 단위 처리 순서도 '판정→억제→상태추적' 일괄에서 줄마다 완결로 바꿨다
    (한 덩어리에 왕복이 들어오면 순서가 무시됐다).
  - 접은 재발은 버리지 않고 `drain_repeats()` → `RealtimeMonitor.bump_repeats()` 로 원래 경고의
    반복 횟수로 남긴다 — 30초간 열 번 흔들린 링크가 '한 번 내려갔다'로 읽히면 안 된다.

- **재실행마다 같은 사건이 쌓였다** (`engine/realtime_monitor.py`)
  - 감시 시작 시 세션 로그의 마지막 256KB 를 다시 판정하는데(seed) `alert_id` 는 프로세스마다
    새로 발급되어 저장본의 id 대조로 걸러지지 않았다. 자동시작이 켜져 있으면 앱을 켤 때마다
    어제 친 `no vlan 100` 이 한 건씩 늘었다. history 경고를 **내용 서명**으로 중복 제거하고,
    저장본 복원 시에도 서명을 등록한다. 라이브 경고는 서명 중복 제거 대상이 아니다.
  - history 경고의 `ts` 를 `--:--:--` 로 둔다 — 되짚어 읽은 구간에서 '판정을 돌린 시각'은
    거짓말이다(어제 친 명령이 '지금 15:54 발생'으로 찍혔다).

- **규칙 엔진이 실시간 경로에 없었다** (`engine/realtime_rule_stream.py` 신설)
  - `RealtimeMonitor` 는 처음부터 규칙 경고를 전제로 만들어져 있었다(rule_id 로 묶기, 우클릭
    '이 규칙 숨기기', 고정 항목 check_id). 그런데 rule_id 를 만들어 주는 곳이 점검 직후 1회
    배치뿐이어서 `config/log_rules.json` 의 서명 수십 개가 사장돼 있었다.
  - 역할 분리: diff 엔진은 '작업자가 무엇을 바꿨나', 규칙 스트림은 '장비가 무엇을 말하나'
    (`show mlag` 의 `state: Inactive`, 비정상 재기동, 인터페이스 oper down 등).
  - 노이즈 문턱은 실측으로 정했다. 실제 세션 로그 2,977줄에서 규칙 판정 70건 중 66건이
    설정 문구와 도움말 사전이었다 → major 이상만 올리고, 아래 두 게이트로 끊어 **4건**(전부
    진짜)이 됐다.
  - `engine/log_rule_engine.py` 게이트 2개:
    * `ContextTracker._CONFIG_CMD_RE` 가 축약형을 받는다. 작업자는 `show run` 이라고 치는데
      전체 이름만 보던 패턴에서는 `is_config` 가 False 로 남아 **설정 원문 전체가 상태 출력으로
      판정됐다**(running-config 의 `no service interface inactive …` 한 줄이 major 로 32회).
    * `is_help` 신설 — `?` 로 끝나는 명령의 출력은 가능한 키워드 목록이다. 도움말에는
      errdisable·inactive·reload 가 설명문으로 들어 있어 어떤 규칙에 걸리든 사실이 아니다.

- **스레드 안전** — `BaselineDiffEngine` 에 RLock 추가(감시 스레드가 판정하는 동안 다른
  스레드가 `reset_context()`/`open_conditions()` 를 부를 수 있다).

- **진단 모달** — 하위 폴더 파일도 목록에 나오므로 상대 경로(`rel`)를 표시하고, 삭제 대상이
  아닌 그 행은 선택 자체를 막았다(누른 뒤에야 안 된다는 것을 알게 되지 않도록). 이름만으로
  가리킬 수 없는 삭제 요청에는 이유를 밝힌다.

- **config session(스테이징) 변경을 확정 변경과 구분** (`engine/baseline_diff_engine.py`)
  - 실제 세션 로그에서 작업자가 `conf session reset` 안에서 `no vlan 4093` / `no mlag` 를 쳤다.
    Arista 의 config session 은 `commit` 할 때까지 적용되지 않는데, 예전에는 그 줄이 곧바로
    CRITICAL '삭제 명령 감지'로 올라갔다 — 아직 아무것도 지워지지 않았는데 즉시 조치 대상이
    됐고, 반대로 실제로 적용되는 순간(`commit` 한 단어)에는 아무 경고도 나오지 않았다.
  - 프롬프트 `config-s-<name>` 을 세션 문맥으로 읽는다(`config-s-w1-if-Et1` 처럼 세션 안에서
    인터페이스에 들어간 경우도 대상을 잡는다). 세션 안의 변경은 심각도를 한 단계 낮추고
    `[예정 · 세션 X]` 로 표시하며, **상태추적을 열지 않는다** — 존재하지 않는 장애를 세워 두면
    되돌릴 복구 이벤트가 없어 영구히 fail 로 남는다.
  - `commit`(세션 안) / `configure session <name> commit`(밖) 을 보면 원래 심각도로 승격하고
    그때 상태추적이 열린다. `abort` 면 화면에 올린 '예정' 경고를 해제한다(지우지 않는다 —
    위험한 변경을 준비했다가 되돌린 것도 점검 이력이다).
  - dedupe 키에 `staged` 를 넣었다 — 예정과 확정은 같은 (장비, type, target)이지만 다른
    사건이라, 넣지 않으면 예정 경고가 직후의 확정 변경을 창 안에서 통째로 삼킨다.

- **능동 상태 폴링 — 두 번째 입력원 신설** (`engine/state_poller.py`, `api/log_analysis_run_api.py`)
  - 세션 로그 tail 로는 링크·라우팅 인접·MLAG 를 알 수 없다. 그건 syslog 에서만 나오고 syslog 는
    작업자가 장비에서 `terminal monitor` 를 켜야 세션에 에코된다 — 실제 CRT 로그에는 0줄이었다.
    즉 감시의 절반이 우리가 통제할 수 없는 설정에 달려 있었다. 이 폴러가 그 의존을 끊는다.
  - **읽기 전용 조회만** 보낸다: `show interfaces status` / `show ip bgp summary` /
    `show ip ospf neighbor` / `show mlag`. 감시가 감시 대상을 변경하는 일은 없어야 한다.
  - **절대 상태가 아니라 전이만 보고한다.** 현장에는 원래 내려가 있는 포트가 흔해서 매 주기
    그것을 올리면 화면이 즉시 무의미해진다. 첫 폴링은 기준을 세우고, 그때 이미 이상인 것은
    `history` 로 표시해 토스트를 띄우지 않는다(CRTStreamWatcher seed 와 같은 규칙).
    관리자가 내려 둔 포트(`disabled`)는 의도된 상태라 제외하고, 한 번에 8개 이상이 동시에
    내려가면(재부팅·모듈 리셋 — 원인은 하나다) 한 줄로 묶는다.
  - **판정은 `BaselineDiffEngine.ingest_state_events()` 를 통과한다.** 폴러가 alert 를 직접
    만들면 평행 세계가 두 개 생긴다. 같은 (device, component_id) 축을 쓰기 때문에 **출처가
    달라도 서로 취소된다** — 폴링이 잡은 LINK_DOWN 을 나중에 도착한 syslog LINK_UP 이 해제하고,
    반대로 syslog 로 잡힌 장애를 폴링이 '이제 정상'으로 확인해 해제한다.
  - **조용한 폴링도 관측으로 센다.** 전이가 없는 것은 '아무것도 못 봤다'가 아니라 '봤고 정상
    이다'이므로, 그 사실이 링크/인접/MLAG 체크리스트를 '판정 불가'에서 풀어 준다
    (`CHECK_ITEMS` 의 입력원에 `polled` 추가).
  - 라우팅 인접만 폴러 안에서 따로 읽는다. `parsers/show_routing_neighbor.py` 는 '마지막 컬럼이
    PfxRcd'라는 전제로 줄 끝에 고정돼 있어 컬럼 수가 다른 레이아웃에 매치되지 않는다(실제
    Arista `show ip ospf neighbor` 는 IP 다음이 VRF 이름이다). 여기서 필요한 것은 컬럼 정렬이
    아니라 '이 피어가 정상인가' 하나이므로 상태 단어의 유무로 판정한다 — Arista/Cisco 양쪽 통과.
    읽을 것이 없으면(`% BGP inactive`) 아무 상태도 만들지 않는다(구성되지 않은 기능을 장애로
    잡지 않는다). MLAG/인터페이스 파서는 기존 것을 그대로 쓰고, 워크스페이스의 실제 출력으로 검증했다.
  - **기본값은 꺼짐**이다 — 주기적으로 운영 장비에 SSH 접속을 만드는 외부 동작이므로 사용자가
    켠다(`config/realtime_watch.yaml` 의 `state_poll`). 상단바에 '상태 폴링' 토글을 두고,
    접속/인증이 실패하는 장비가 있으면 토글 자체에 색과 사유를 얹는다 — '켰으니 되고 있다'로
    읽히면 안 된다. 주기는 15~900초로 제한한다.
  - 상단 상태줄 문구도 함께 정리했다: 폴링이 도는 장비는 syslog 가 없어도 판정 가능하므로
    'syslog 없음 N대'가 아니라 '상태 판정 불가 N대'(두 근거가 모두 없는 장비)를 센다.

- **회귀 테스트 7파일 111건 추가**
  - `tests/test_realtime_watch_dir.py`(15) — 감시 폴더 불변·점검 결과 파일 제외·watch_dir 실측
  - `tests/test_realtime_line_classify.py`(27) — 입력/출력 분류·오탐·오취소·프롬프트 문맥
  - `tests/test_realtime_observability.py`(12) — 판정 불가 유지·감시 품질·SPANTREE
  - `tests/test_realtime_flap_and_history.py`(10) — 방향 판정·플랩·재실행 중복
  - `tests/test_realtime_rule_stream.py`(18) — 규칙 스트림·도움말/설정 게이트·문턱
  - `tests/test_realtime_config_session.py`(14) — 예정/확정/폐기 경계
  - `tests/test_realtime_state_poller.py`(19) — 전이만 보고·양방향 취소·실제 Arista 출력 파싱

### 프로파일 단위 '가상환경' 옵션

가상 장비 대응을 로그 문구 추론에만 의존하지 않고, 회차(프로파일)마다 명시할 수 있게 했다.
문구는 벤더/버전마다 다르고 명령이 아무 것도 출력하지 않는 경우도 있어 오탐이 남았기 때문이다.

- **프로파일 메타에 `is_virtual`** (`engine/profile_manager.py`)
  - `data/<고객사>/<프로파일>/profile/profile.json` 이 단일 출처. `read_meta()`(복구·생성 없이
    읽기만), `is_virtual()`, `set_virtual()` 추가. 기본값은 **실제 장비(False)** — 기존 프로파일이
    갑자기 하드웨어 점검을 건너뛰지 않는다.
- **보고서: 하드웨어 항목 자동 '해당없음'** (`config/inspection_report_template.yaml`,
  `report/inspection_excel.py`, `engine/inspection_report_builder.py`)
  - 템플릿 항목에 `virtual_na: true` 필드 신설(Power/FAN/Module/Temperature/Transceiver).
    가상환경 프로파일이면 로그를 보지 않고 `해당없음 (가상환경 — 해당 하드웨어 없음)`으로 판정한다.
  - `build_context()`가 프로파일 설정을 읽어 `evaluate_device(..., is_virtual=)`로 넘긴다.
- **AI 원본로그 분석 프롬프트 2종** (`ai_analysis/raw_log_analyzer.py`, `api/log_analysis_run_api.py`)
  - 기존 vEOS-lab 프롬프트는 가상환경 전용으로 남기고, 물리 장비용 프롬프트를 새로 뒀다.
    예전에는 실기 점검에서도 "하드웨어/전원/온도 이상은 가상 플랫폼 제약이니 무시하라"는 지시가
    나가서 실제 PSU 고장이 보고되지 않을 수 있었다. 로컬 AI의 `[PLATFORM]` 줄도 함께 바뀐다.
- **UI** (`web_ui/js/core-profile-modal.js`, `inspection-report.js`, `style.css`)
  - 정기점검 추가 시 가상환경 여부를 묻고, 프로파일 카드에 '가상환경' 배지 + 전환 버튼
    (`set_inspection_profile_virtual`). 보고서 탭에도 가상환경 안내 한 줄.


가상 장비(vEOS-lab)로 점검하면 하드웨어·기능이 없어서 나오는 출력이 전부 '확인필요'로
보고됐다. 같은 항목이 실기 장비(Studio Vird)에서는 정상 판독되므로 장비 상태가 아니라 판정
로직의 문제였다. 보고서 표기(Hostname/IP/요약표/장비목록)도 함께 정리했다.

- **판정 상태에 '해당없음' 추가** (`report/inspection_status.py`)
  - 기존 4-state(정상/확인필요/미수집/접속 불가)에 `STATUS_SKIP = "해당없음"`을 더했다.
    미수집("봐야 하는데 못 봤다" — 다음 회차 재시도)과 해당없음("볼 것이 없다" — 조치 불필요)은
    둘 다 값이 없지만 원인과 조치가 정반대라 한 칸에 담으면 안 된다.
  - `NOT_JUDGED_STATUSES`(미수집/해당없음/접속 불가)를 함께 내보낸다 — 요약표에서 정상 칸에도
    비정상 칸에도 넣으면 안 되는 것들의 단일 출처.

- **Power / FAN / Temperature / Module — 가상 플랫폼 미지원을 해당없음으로** (`config/log_rules.json`)
  - 서명 `command_unsupported_platform` 추가: `% Unavailable command (not supported on this
    hardware platform)` → info / `not_applicable`. **반드시 `command_rejected` 앞에** 둔다
    (한 줄이 두 패턴에 모두 걸리는데 배열 순서가 우선순위라, 뒤에 두면 '플랫폼 미지원'이라는
    사실이 사라진다).
  - `resource_absent`(PSU/FAN/센서 없음)를 `collection` → `not_applicable`로 옮겼다.

- **BGP / EVPN — 기능 미구성은 Fail 이 아니라 해당없음** (`config/log_rules.json`)
  - `protocol_not_running` 패턴을 EVPN/MPLS/VRRP/VARP와 `is inactive`·`not active` 형태까지
    넓히고 category를 `not_applicable`로 바꿨다. LAB1처럼 BGP/EVPN 구성 자체가 없는 장비에서
    `% BGP inactive`가 비정상으로 잡히던 것이 사라진다.

- **STP — VendorDriver 의 커맨드 생성 버그** (`plugins/vendors/arista.py`, `cisco.py`)
  - `stp_status`가 `show spanning-tree vlan 1,100,200,999`로 특정 랩의 VLAN 번호를 하드코딩하고
    있었다. 그 VLAN 이 없는 장비(vEOS-lab 의 10/20/200/4000 등)에서는 EOS 가 `% Invalid input`
    을 돌려주므로, **STP 상태를 한 줄도 못 읽은 채 '비정상' 판정**이 나갔다 — Finding 자체가
    무효였다. 인자 없는 `show spanning-tree`로 바꿨다(모든 VLAN 섹션이 한 번에 나오고,
    `parsers/show_spanning_tree.py`의 `split_combined_vlan_output()`이 이미 그 형식을 자른다).

- **Log 확인 — 심각도 필터 적용** (`report/inspection_excel.py`)
  - `%SYS-5-CONFIG_I`(콘솔 로그인) / `%SYS-5-CONFIG_E`(설정모드 진입) 같은 정상 운영 로그가
    장비당 29~31건씩 '특이 로그'로 계수됐다. 이제 major/critical 만 특이 로그로 세고,
    minor/info 는 `참고 N건`으로만 남긴다(집계에서 빠지되 사라지지는 않는다).

- **Interface — SVI 의 lowerlayerdown 을 실제 장애와 구분** (`config/log_rules.json`)
  - 서명 `svi_lowerlayerdown` 추가(minor / `topology`). Vlan10/20/200/4000 의 lowerlayerdown 은
    SVI 자체의 장애가 아니라 '그 VLAN 에 up 인 멤버 포트가 없다'는 하위 계층의 결과다. 랩
    토폴로지에서는 정상이므로 major 로 올리면 매 회차 오탐이 된다. 실제 단절이면 그 물리
    포트에서 `interface_line_down`이 따로 잡힌다. `svi_down`(hard down)은 critical 그대로.

- **Free Memory — 값이 0.126 으로 찍히던 문제** (`report/inspection_excel.py`)
  - `(Free/Total)*100` 계산식 자체는 맞았고, **표시값만 비율(0~1)이었다.** 이제 사람이 읽는
    `value`는 `"12.6%"`, 엑셀 셀용 `number`는 0.126(number_format `0.0%`)으로 나눠 낸다.
  - `warn_at` 을 `30`(퍼센트)으로 적어도 `0.3` 과 같게 동작한다 — 둘이 섞이면 판정이 조용히
    '항상 정상'이 된다.

- **Hostname 에 raw 타임스탬프가 들어가던 문제 + IP 열 전 행 공란**
  (`engine/inspection_report_builder.py`)
  - `latest_logs_by_device()`가 레거시 파일명(`AutoCheck_*`)만 아는 자체 정규식을 갖고 있어
    현재 규칙(`20260805_095518_raw_Agg1.txt`)이 한 건도 매칭되지 않았다. 폴백으로 파일명 전체가
    장비명이 되어 보고서에 `20260805_095518_raw_Agg1`이 찍혔고, 그 이름은 장비목록의 `Agg1`과
    매칭되지 않으니 IP·모델·용도가 전부 빈칸이었다. 파일명 해석을 `core/log_naming.py`
    단일 출처로 되돌렸다 — **이 한 줄이 Hostname 과 IP 를 동시에 고친다.**
  - 장비목록 조인을 대소문자·공백 무시로 완화(`_inventory_record()`), 그래도 IP 가 없으면
    원본로그의 관리 인터페이스에서 주워 온다(`_ip_from_sections()`).

- **요약표에 '미수집' 열 추가** (`report/inspection_pdf.py`, `report/inspection_excel.py`)
  - 헤더는 `정상/비정상` 2개인데 숫자는 `14 12 2`처럼 세 칸에 걸쳐 찍혀서 세 번째 숫자가
    무엇인지 알 수 없었다. PDF 요약표에 `미수집/해당없음` 열을, 엑셀 점검요약 시트에
    `점검항목/정상/비정상/미수집·해당없음` 4열을 추가했다.
  - 집계도 함께 고쳤다. 예전 `passed = total - fail` 은 판정하지 못한 항목을 전부 정상으로
    합산해서, 절반이 미수집인 가상 장비도 요약만 보면 정상 점검된 것처럼 보였다. 실측
    항목(CPU/Memory/Uptime)도 값을 못 뽑았으면 미수집으로 센다.

- **장비목록/지원목록 페이지의 placeholder 제거**
  - 장비목록에 `location`(위치) / `warranty` 필드를 추가하고(`engine/device_inventory_core.py`,
    엑셀 내보내기 헤더, 장비 목록 화면의 입력 칸) PDF '장비 목록' 페이지의 위치·Warranty 열이
    그 값을 쓰게 했다 — 두 열은 채울 데이터 자체가 없어 항상 공란이었다.
  - 지원이력은 PDF 를 만든 회차만 기록해서 처음 뽑으면 한 줄뿐이었다. 이제 같은 고객사의 지난
    회차 `reports/_snapshot.json`을 되짚어 빠진 줄을 채우고(`_past_inspections()`), 비고에
    점검 대수/확인필요 대수를 남긴다.

- **소견 문구 분리** — 미수집/해당없음뿐인 장비까지 '확인 필요'로 적으면 실제로 손봐야 하는
  장비와 구분이 안 된다. `이상 없음 (미수집 N항목 재점검 권고)` 형태로 갈라 적는다.

- 회귀 테스트 `tests/test_inspection_report_status.py`(17건) /
  `tests/test_inspection_report_identity.py`(8건) 추가. 규칙 개수 골든
  (`tests/test_log_rule_engine_golden.py`)은 서명 31 → 33 으로 갱신.

## v0.5.8 (현재) — 실시간 감시 '파일 진단' 모달에 다중 선택/삭제/폴더 열기 추가

- **`실시간 감시 → 파일 진단`(CRT 로그 파일 진단 모달)에 다중 선택·삭제·폴더 열기 추가**
  - `web_ui/js/realtime-monitor-panel.js` — 행을 드래그로 범위 선택하거나 Shift+클릭으로
    범위 선택, Ctrl(Cmd)+클릭으로 개별 토글, Ctrl(Cmd)+A로 전체 선택할 수 있다. 목록 가장자리로
    드래그하면 자동 스크롤된다(`core.js`의 `createDragRangeSelect` 공용 — 점검 로그 뷰어 등
    기존 3곳과 같은 계약). 체크박스도 별도로 눌러 토글 가능.
  - `api/log_analysis_run_api.py` — `delete_realtime_log_files()`(선택 삭제), 
    `open_realtime_log_folder()`(CRTlog 폴더를 OS 탐색기로 열기) 추가. 삭제는 파일명만 받아
    `os.path.basename()`으로만 다뤄 경로 조작을 막고, **지금 감시가 tail 중인 파일은 건너뛴다**
    (오프셋 추적이 깨질 수 있어 감시를 먼저 멈추라고 안내).
  - 헤더의 클래스 오타(`rtm-alert-modal-head` → 올바른 `rt-alert-modal-head`)도 함께 고쳤다 —
    버튼을 하나 더 넣으면서 보니 이 모달만 헤더 레이아웃(flex 정렬)이 안 먹고 있었다.
  - 회귀 테스트 `tests/test_realtime_probe_files.py` 추가(삭제/경로조작 방지/추적 중 파일 보호 7건).

## v0.5.7 — 오류 해결 후에도 남아 있던 3-Way 교차 강조 잔상 수정

- **실시간 오류 분석에서 오류를 해결/무시/초기화해도 장비 로그 박스·체크리스트의 강조
  테두리가 그대로 남던 버그 수정** (`web_ui/js/realtime-monitor-panel.js`)
  - 원인: 오류 카드를 클릭/hover하면 `rtmXhlPinned`/`rtmXhlHover`에 그 오류가 걸린 장비 목록을
    저장해 두고, `applyRtmCrossHighlight()`는 그 장비 이름이 지금 DOM에 있는지만 보고 테두리를
    입혔다 — "그 오류가 아직 존재하는지"는 확인하지 않았다. 그래서 오류를 해결한 뒤에도(장비
    자체는 여전히 화면에 있으므로) 예전 강조가 계속 남았다.
  - 수정: `renderRtmAnalysis()`가 매 폴링마다 지금 화면에 실제로 있는 오류 키(대분류 줄 +
    선택된 그룹 안의 장비별 상세)를 모으고, 고정/미리보기가 더 이상 존재하지 않는 키를
    가리키면 지운다.

## v0.5.6 — 실시간 감시 내역의 프로파일 격리 · 오류 분석 목록 묶음 단위 수정

- **실시간 오류 분석 목록이 성질이 다른 오류를 한 줄에 뭉치던 문제 수정**
  - 증상: 목록에 "기타 / 7대 · 92건" 한 줄이 떠 있는데 열어 보면 '비정상 재기동'·'인증 실패'·
    '카운터 증가'가 섞여 있었다. 묶는 키가 category 였고, 체크리스트 항목에 매핑되지 않는
    규칙 엔진 경고는 전부 category 없음(기타)으로 떨어졌기 때문이다. 같은 category 안에
    성질이 다른 판정이 공존하는 경우도 합쳐졌다(MLAG peer-link split-brain ↔ STP/MLAG 상태 변화).
  - `engine/realtime_monitor.py` — finding 마다 `group_key`/`group_label`을 붙였다. 규칙 기반
    경고는 `rule_id`까지 내려가 쪼개고(같은 규칙은 장비가 몇 대든 한 줄), 구조적 판정 6종은
    각자의 키를 갖는다. 목록 이름은 규칙 id 대신 규칙 문구(`비정상 재기동 이력`)를 쓰고,
    줄마다 문구가 갈리는 키워드/syslog 판정만 규칙 이름으로 물러난다.
  - finding 상한을 40 → 200으로 올리고 잘라낸 몫을 `findings_dropped`로 알린다 — 묶음이
    '장비 x 규칙' 단위로 세분화되면서 40개로는 뒤쪽 규칙이 통째로 사라졌다.
  - `web_ui/js/realtime-monitor-panel.js`, `style.css` — 목록을 `group_key`로 묶고, 굵은 주
    라벨을 '그 오류가 무엇인지'로 바꿨다(큰 기술 분류는 머리줄의 작은 태그로 분리).
  - 회귀 테스트 `tests/test_realtime_finding_groups.py` 추가(묶음/분리 6건).

- **프로파일을 바꿔도 이전 프로파일의 실시간 감시 내역이 그대로 남던 버그 수정**
  - `api/project_core_api.py` — `set_active_project()` 등 활성 프로파일이 바뀌는 모든 경로가
    `_activated_profile()`을 지나게 했다. 활성 컨텍스트 캐시(`core/context_cache.py`)는 만료가
    없는데 이 경로가 그것을 비우지 않아, 활성 프로젝트 파일은 바뀌었는데도
    `resolve_active_customer_profile_names()`가 이전 프로파일을 계속 돌려주고 있었다
    (실시간 감시 저장 경로가 안 바뀌므로 '프로파일이 바뀌었다'는 판정 자체가 성립하지 않았다).
  - `api/log_analysis_run_api.py` — 감시가 도는 중에도 프로파일을 따라가도록 바꿨다(전에는 물러났다).
    자동 시작을 켜 두면 감시가 항상 돌고 있어 전환이 영구히 무시됐고, 새 프로파일의 경고까지
    이전 프로파일 파일에 쌓였다. 이제 전환 시 이전 프로파일 상태를 저장하고, 새 프로파일의
    Baseline·장비 목록·저장 위치로 감시를 다시 시작한다. 전환 직후 즉시 반영되도록
    `notify_active_profile_changed()` 훅을 추가했다(폴링 동기화는 3초로 묶여 있다).
  - `web_ui/js/realtime-monitor-panel.js` — 프로파일이 바뀌어 장비 목록이 달라지면, 이전
    프로파일에서 체크해 둔 감시 대상 선택을 버리고 새 목록으로 다시 고른다.
  - 회귀 테스트 `tests/test_realtime_profile_state.py` 추가(전환 경계 4건).

## v0.5.5 — 실시간 감시 엔진 4개 모듈 통합, UI/UX 고도화 및 연쇄 상관규칙 확장

- **Module 1 — 세션 경로 격리 및 Baseline 자동 갱신**
  - 수동 CRT 로그 오염으로 인한 감시 무력화 버그 수정: 파일명 규칙(`{stamp}_raw_{device}.txt`) 적용 및 혼합 소스 폴백(`source_kind: "mixed"`) 지원
  - 점검 완료 후 무중단 스냅샷 교체를 통한 Baseline 자동 갱신 로직 적용
  - 필터 영속화 관련 API 6개 추가 및 취소 push 연동 (`api/log_analysis_run_api.py`, `api/terminal_inspection_api.py`)
- **Module 2 — 취소 패턴 추가 및 상태 추적 정교화**
  - `StateTracker` 클래스를 도입해 `alert_id` 및 `component_id` 기반 경고 관리, 근본원인 주석 지원 (`engine/baseline_diff_engine.py`)
  - MLAG peer-link, VLAN 재등록, OSPF FULL 취소 이벤트 패턴 추가
  - 기존 OSPF 패턴 버그(ADJCHG 인식 오류 및 네이버 IP 미캡처) 수정, 네이버 IP별 상태 추적 분리
  - 경고 취소 시 실시간 UI 갱신 반영 (`engine/realtime_monitor.py`)
- **Module 3 — L2/L3/Overlay 신규 서명 및 연쇄 상관규칙 확장**
  - 상관규칙 속성 `any_of`, `scope: global`, `root_cause_intent` 적용 지원 (`engine/log_rule_engine.py`)
  - VLAN, SVI, VARP, VXLAN, EVPN 등 신규 서명 11개와 연쇄 상관규칙 5개 추가 (`config/log_rules.json`)
  - 구체적 규칙이 우선 적용되도록 서명 순서 재배치 및 기존 mlag_gateway_impact 규칙 버그 개선
- **Module 4 — 실시간 감시 UI 요소 및 영속화 추가 (`web_ui/js/realtime-monitor-panel.js`, `style.css`)**
  - 감시 패널에 고정 카드, 우클릭 메뉴(장비 숨기기, 규칙 숨기기 등), 표시 설정 모달 추가
  - 해제 수신 핸들러 연동(`web_ui/js/realtime-baseline-alerts.js`)으로 취소 이벤트를 UI 토스트 및 이력 모달에 즉시 반영
  - 분석 finding 원인 문구 표시, 필터/고정 상태의 YAML 영속화(`config/realtime_watch.yaml`) 적용

## v0.5.3 — SecureCRT 세션 로그 실시간 Baseline 감시 · 전용 탭 분리 · 로그-장비 매칭 정상화

- **실시간 감시 파이프라인 신규 (`core/crt_stream_watcher.py`, `engine/baseline_store.py`, `engine/baseline_diff_engine.py`, `engine/realtime_monitor.py`)**
  - `CRTlog/`의 파일별 바이트 오프셋을 추적해 새로 덧붙여진 차분만 0.3초 간격으로 추출(전체 재파싱 없음)
  - `00_orignal_log`의 사전 점검 결과를 Baseline 스냅샷(VLAN/인터페이스/라우트/BGP 네이버)으로 메모리 로드
  - config 모드 문맥(`interface Et1` → `shutdown`)을 추적하는 Stateful Diff 판정 + 동일 경고 10초 중복 억제
  - 장비 × 7개 항목 실시간 체크리스트와, 설정 변경→DOWN 인과를 묶는 규칙 기반 원인 분석
- **실시간 감시 전용 탭 분리 (`web_ui/js/realtime-monitor-panel.js`)**
  - '세션 터미널' 탭 하단에 얹혀 있던 패널을 사이드바 독립 탭으로 이동 — 터미널 카드·접속 대상 목록과
    세로 공간을 다투던 문제를 해소하고, 감시 대상 선택을 터미널 접속 체크박스와 분리
  - 좌우/상하 구분선 비율·보기 모드·선택 장비를 `config/realtime_watch.yaml`에 저장해 재실행 후에도 유지
    (pywebview는 `file://` 오리진이라 localStorage를 신뢰할 수 없어 앱이 직접 저장)
  - 우측 하단 심각도별 경고 토스트(`web_ui/js/realtime-baseline-alerts.js`) 및 세부 이력 모달
  - `프로그램 실행 시 자동 시작` 체크박스 — `main.py`가 창 생성 직후 감시를 시작
- **로그 파일 ↔ 장비 매칭 정상화 (`engine/stream_device_matcher.py`) — 실시간 감시가 전혀 동작하지 않던 원인**
  - SecureCRT가 파일명을 접속 IP로 남기면(`192.168.205.101_...txt`) 장비명 파싱 결과가 인벤토리와
    불일치해 모든 차분이 조용히 버려졌다. 파일명 IP ↔ 장비 목록 IP 대조를 추가
  - 로그 내용의 `! device: X` 헤더와 프롬프트 `X#`로 판정하는 폴백 추가 — `session.log`처럼 파일명에
    단서가 없는 경우를 구제
  - 감시 확장자에 `.log` 추가(SecureCRT 기본 세션 로그 이름이 `session.log`) 및 하위 폴더 1단계 스캔
  - 감시 시작 후 새로 생긴 파일은 EOF가 아니라 **첫 줄부터** 읽는다 — 프로그램을 켠 뒤 CRT를 열면
    초기 명령이 통째로 유실됐다
  - 감시 시작 전부터 있던 파일은 최근 일부를 화면에만 시딩(`--:--:--`)하고 판정에서 제외 — 화면이
    비어 보여 '로그를 읽지 못한다'고 오해하던 문제 해소
  - 식별 실패 파일을 상태로 노출하고, `파일 진단` 모달에서 파일별 매칭 근거를 확인 가능

## v0.5.0 — AutoCheck v0.5.0 마이그레이션 · 프로젝트 구조 및 문서 링크 정체성 업데이트

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

