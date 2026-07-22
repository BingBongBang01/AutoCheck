# CHANGELOG

버전 규칙: 수정할 때마다 0.0.1씩 증가. 인사평가 KPI 계획상 v1.0.0은 8월(v1.0 단계, "핵심 기능 + 전체 완성") 전까지 올리지 않음 — 지금은 전부 0.0.x 범위.

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

