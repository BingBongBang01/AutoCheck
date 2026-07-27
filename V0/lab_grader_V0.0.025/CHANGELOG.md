# CHANGELOG

버전 규칙: 수정할 때마다 0.0.1씩 증가. 인사평가 KPI 계획상 v1.0.0은 8월(v1.0 단계, "핵심 기능 + 전체 완성") 전까지 올리지 않음 — 지금은 전부 0.0.x 범위.

## v0.0.15 (현재) — 워크스페이스 자동 마이그레이션 + 프로덕션 준비도 리뷰

**레거시 → 신규 워크스페이스 자동 마이그레이션 (`engine/migration_manager.py`, 신규)**
- 앱 시작 시(`gui_web.py`, `webview.create_window()` 이전) 1회 자동 실행. 레거시 데이터
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

