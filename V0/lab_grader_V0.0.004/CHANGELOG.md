# CHANGELOG

버전 규칙: 수정할 때마다 0.0.1씩 증가. 인사평가 KPI 계획상 v1.0.0은 8월(v1.0 단계, "핵심 기능 + 전체 완성") 전까지 올리지 않음 — 지금은 전부 0.0.x 범위.

## v0.0.4 (현재)
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
