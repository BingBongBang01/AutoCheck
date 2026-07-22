# LAB1 자동채점 — v0.0.4

> 버전 이력은 `CHANGELOG.md` 참고. 실행 안 되면 `실행방법.txt` → `python setup_check.py` 순으로 확인.
> **신규(v0.0.4): `python gui.py`로 실제 데스크톱 UI 실행 가능** — CLI(`main.py`) 대신 이걸 써도 됨.

## 지금 실제로 검증된 것 (이 환경에서 직접 실행함)

1. **`unl_parser.py`** — `04_TEST.unl`을 실제로 분석해 다음을 자동 확인함:
   - 7대 노드, 물리 링크 14개 (Port-Channel 설계 Po1/Po2/Po10/Po20과 일치 확인)
   - 이미지버전 위험 감지(veos-4.35.4M, 7대 전부) — 실행: `python3 unl_parser.py labs/lab1_campus/04_TEST.unl`
   - 좌표 기반 계층(tier) 자동 추론 — 장비명 하드코딩 없이 Core/Agg/Access 3단 정확히 구분
2. **`parsers/show_vlan.py`, `parsers/show_spanning_tree.py`** — 단독 파싱 검증 + **콤보 출력 분리(`split_combined_vlan_output`) 검증 완료** (한 커맨드에 여러 VLAN 섹션이 이어 나오는 실제 장비 출력 형태 재현해서 테스트)
3. **`engine/comparator.py`, `engine/scorer.py`, `engine/history.py`** — `main.py --mock`으로 **원본 텍스트 → 파서 → 대조 → 채점 → 이력저장까지 전체 파이프라인 실제 실행 검증함.** D12 세션에 기록된 실제 상태(VLAN 완료/STP 미적용, Access1·Access3 오선출)를 재현 데이터로 넣고 돌려서 정확히 일치하는 결과 확인:
   - VLAN 18/18 PASS
   - STP 3/14 PASS (우선순위 미적용 8건, root 오선출 3건 — 전부 실제 문제와 일치)
   - LACP 이하 5단계는 STP 미완료로 자동 SKIPPED 처리 확인
   - `history/lab1_campus/{timestamp}.json`에 결과 저장 확인
4. **`engine/collector.py`의 `pre_flight_check()`** — 성공/실패/빈IP 3케이스 단위테스트 + `collect_all()` 통합 시 사전점검 실패 즉시 중단 확인
5. **병렬 연결 방식 확인** — netmiko(paramiko 기반)라 cmd/PowerShell 창이 뜨지 않음. 파이썬 프로세스 안에서 소켓 연결만 맺는 방식이라 전부 백그라운드에서 조용히 병렬 처리됨.
6. **`engine/benchmark.py`** — 병렬 연결 수 제한 없앰(`lab_meta.yaml`의 `max_parallel_workers`는 이제 숫자든 몇이든 자유). 워커 수를 바꿔가며 실제 소요시간을 측정해서 "최속 대비 10% 이내에서 가장 적은 리소스"를 자동 추천하는 로직을 가짜 지연시간으로 시뮬레이션 검증함.

## 아직 실행 못 한 것 (본인 노트북에서 직접 실행 필요)

- **실제 장비 접속** — 이 환경(샌드박스)은 회사 내부망(EVE-NG)에 접속 불가. `python3 main.py`(mock 옵션 없이)를 본인 노트북에서 실행하면 실제 현재 상태를 가져옴.

## 지금 당장 본인 노트북에서 할 일

```bash
pip install netmiko pyyaml --break-system-packages

# 1. labs/lab1_campus/ip_allocation.yaml 에 실제 Mgmt1 IP 채우기
# 2. connection.yaml의 check_target_node(Core1)가 실제로 맞는지 확인
# 3. (선택) 병렬 연결 수 추천값 산출 후 lab_meta.yaml에 자동 반영
python3 -m engine.benchmark --apply

# 4. 실행
python3 main.py
#    (장비 접속 없이 파이프라인만 다시 확인하고 싶으면: python3 main.py --mock)
```

## 파일 구조 (갱신)
```
lab_grader/
├── main.py                    # 실행 완료 (--mock), 실장비는 본인 노트북에서
├── unl_parser.py               # 실행 완료
├── demo_run.py                 # 실행 완료 (comparator/scorer만 단독 검증용, main.py --mock이 더 최신)
├── connection.yaml             # VPN/사전점검 설정, 실행 완료
├── parsers/
│   ├── show_vlan.py            # 실행 완료
│   └── show_spanning_tree.py   # 실행 완료 (콤보 분리 포함)
├── engine/
│   ├── collector.py            # pre_flight_check 실행 완료, 실제 수집은 본인 노트북에서
│   ├── comparator.py           # 실행 완료
│   ├── scorer.py                # 실행 완료
│   └── history.py               # 실행 완료 (JSON 저장 확인됨)
├── history/lab1_campus/         # 실행 결과 저장됨
└── labs/lab1_campus/
    ├── 04_TEST.unl
    ├── discovery_result.json    # unl_parser 실행 결과
    ├── target_state.yaml
    ├── stages.yaml
    ├── lab_meta.yaml
    └── ip_allocation.yaml       # IP 비어있음, 채워야 함
```

## 오늘 발견하고 수정한 버그 4가지

1. **[심각] 커맨드 목록 4중 정의** — `stages.yaml`/`main.py`(2곳)/`collector.py`에 커맨드 문자열이 각각 하드코딩되어 있었음(`stages.yaml`엔 아무도 안 쓰는 죽은 참조 `"show spanning-tree root"`까지 있었음). `main.py`의 `get_all_commands()`가 `stages.yaml`을 유일한 출처로 삼도록 통일.
2. **[위험] 파싱 실패가 조용히 "정상"으로 처리됨** — STP 우선순위 파싱 실패(`None`)와 실제 기본값(32768)을 comparator가 구분 안 하고 있었음. `UNKNOWN` 상태를 신설해서 명확히 분리.
3. **[잠재 버그] 계층 추론이 좌표 완전일치만 인정** — 캔버스에서 노드를 몇 px만 어긋나게 옮겨도 계층이 잘못 나뉠 뻔했음. 허용오차(30px) 기반 클러스터링으로 수정.
4. **[크래시 위험] `ip_allocation.yaml`이 비어있으면 전체가 죽음** — `collect_device()`의 `load_credentials()` 호출이 `try` 블록 밖에 있어서, 지금처럼 IP가 비어있는 상태로 실행하면 첫 장비에서 바로 예외로 전체가 죽을 뻔했음. `try` 안으로 이동해서 장비별 개별 실패로 처리되게 수정 — 실제로 지금 상태(IP 비어있음)로 재현 테스트해서 정상 동작(7대 각각 실패 사유 리포트, 프로그램 안 죽음) 확인함.

## 프로젝트(랩) 다중 관리 추가

- `engine/project_manager.py` — 프로젝트 추가/이름변경/삭제/선택. `labs/{project_id}/` 폴더 하나가 프로젝트 하나.
- 각 프로젝트는 `lab_meta.yaml`/`target_state.yaml`/`stages.yaml`/`ip_allocation.yaml`/`commands_catalog.yaml`을 전부 독립적으로 가짐 — 실제 테스트로 확인함(한 프로젝트에서 커맨드 토글해도 다른 프로젝트엔 영향 없음).
- 폴더명(id)은 한 번 정해지면 안 바뀜 — 이름 변경은 `project_meta.yaml`의 `display_name`만 바꾸는 방식이라 다른 파일들의 경로 참조가 안전하게 유지됨.
- 한글 등 비ASCII 이름을 슬러그화하면 "project"나 숫자만 남는 문제를 발견해서 수정함 — 이런 저품질 슬러그는 타임스탬프를 붙여 구분되게 처리.
- `config/commands_catalog.yaml`(전역)은 폐기하고 `labs/{project}/commands_catalog.yaml`(프로젝트별)로 전환.

## main.py ↔ 프로젝트 연결 완료

- `python3 main.py --mock` — active_project(project_manager)를 자동으로 따라감
- `python3 main.py --mock --project lab1_campus` — 특정 프로젝트로 명시 전환해서 실행
- 활성 프로젝트가 지정 안 돼있으면 첫 프로젝트를 자동 선택(안내 메시지 출력)
- 프로젝트 해석(`init_project()`)은 `__main__` 블록 안에서만 실행되도록 분리 — 다른 모듈이 main.py를 import해도 프로젝트 상태를 의도치 않게 건드리지 않음
- 3가지 케이스(옵션 없음/명시 지정/미설정 폴백) 전부 실행 검증 완료

## [긴급 수정] Windows 실행 시 UnicodeDecodeError

실제로 발생한 오류: `UnicodeDecodeError: 'cp949' codec can't decode byte 0xec...`

**원인**: Python의 `open()`은 encoding을 명시 안 하면 OS 기본 인코딩을 씀. 한글 Windows는 기본이 UTF-8이 아니라 `cp949`라, UTF-8로 저장된 yaml 파일(한글 설명 포함)을 읽다가 깨짐. Mac/Linux는 기본 인코딩이 UTF-8이라 이 문제가 여기서는 재현이 안 됐던 것.

**수정**: 프로젝트 전체의 `open()` 호출 11곳에 `encoding="utf-8"`을 명시적으로 추가함 (grep으로 전수 확인, 누락 없음 확인).

**추가 방어**: `setup_check.py`에 인코딩 진단 항목 추가 — 실행 전에 이 문제를 미리 잡아줌.
