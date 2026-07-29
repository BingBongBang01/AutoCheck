# AutoCheck — 네트워크 장비 자동 점검/채점 (v0.0.030)

버전 이력은 `CHANGELOG.md`, 구조 설명은 `ARCHITECTURE.md`,
설치·실행 문제는 `실행방법.txt`를 참고하세요.

버전 번호는 폴더명(`V0/lab_grader_V0.0.030`) = `VERSION` 파일 = CHANGELOG 제목이
항상 같은 값을 씁니다. UI 좌하단에 표시되는 값도 `VERSION`을 그대로 읽습니다.

## 실행

```bash
pip install -r requirements.txt
python main.py
```

진입점은 `main.py` 하나입니다. 실행하면 Material Design 3 웹 UI 창(pywebview)이
뜨고, 모든 조작은 그 안에서 합니다. CLI 채점 경로와 mock 수집 경로,
기존 Tkinter UI는 전부 폐기했습니다.

무인 정기점검만 예외적으로 UI 없이 돌릴 수 있습니다:

```bash
python -m engine.scheduler          # 지금 시각에 due한 job만 1회 실행
python -m engine.scheduler --loop   # 자체 루프로 매분 체크
```

일정은 `config/scheduler.yaml`에 정의하며, UI 실행과 스케줄러 실행 모두
`engine/grading.py`의 동일한 채점 경로를 씁니다.

## 처음 쓸 때 순서

사이드바는 실제 사용 순서대로 `준비 → 실행 → 결과 → 기타` 네 그룹으로 묶여 있습니다.
위에서 아래로 따라가면 됩니다.

1. `python main.py`로 앱 실행
2. **워크스페이스** — 고객사 추가 → 정기점검 회차(프로파일) 생성 또는 선택
   - 같은 고객사의 **두 번째 회차부터는 직전 회차의 장비목록을 그대로 물려받습니다.**
     장비·IP·계정은 회차가 바뀌어도 대부분 같기 때문입니다.
   - 더 예전 회차에서 가져오려면 프로파일 카드의 **장비목록 복사** 버튼, 또는
     장비 목록 탭의 **다른 회차에서 복사** 버튼을 쓰세요. 이름이 같은 장비는
     건너뛰므로 지금 목록이 지워지지 않습니다.
3. **장비 목록** — 관리 IP·계정 입력, 사용할 장비 켜기
   - IP·포트·계정을 고치면 **자동으로 연결을 확인**합니다. 성공한 행은 초록, 실패한 행은
     빨강으로 표시되고 실패 사유(포트 응답 없음 / 인증 실패)가 그대로 나옵니다.
   - 접속에 성공하면 **장비가 알려준 hostname으로 목록의 이름을 자동으로 맞춥니다.**
     (`AUTO-101` → `Core1`). 다른 장비가 이미 그 이름이면 바꾸지 않고 사유를 알려줍니다.
   - 여러 대를 한 번에 확인하려면 **전체 연결 확인** 버튼을 쓰세요. 불러오기(CSV/Excel)와
     IP 자동 생성 직후에는 자동으로 전체 확인이 돌아갑니다.
   - IP가 비어 있는 장비가 있으면 채점이 시작 전에 중단되고 어떤 장비인지 알려줍니다.
4. **EVE 구성 불러오기** *(선택)* — `.unl` 파일에서 장비 목록을 한 번에 등록
5. **명령어 카탈로그** — 수집할 커맨드 활성화
6. **점검 항목** — 단계(Stage) 구성과 의존관계 확인 (읽기 전용)
7. **세션 터미널** *(선택)* — 장비에 직접 SSH 접속해 수동 확인
8. **수집/채점** — 실행. 접속→수집→파싱→판정→채점→이력저장→리포트까지 한 번에 처리
9. **점검 로그** — 수집된 원본 로그 확인, 이상 탐지, 외부 공유용 마스킹
10. **대시보드 / 발견사항 / 분석 / 보고서 / 이력** — 결과 확인

**환경 설정**(AI 제공자·API 키·터미널 동작·SSH 공통값)은 **모든 고객사·모든 회차에 공통**으로
적용됩니다. 고객사나 회차를 바꿔도 유지되므로 처음 한 번만 맞춰두면 됩니다.

## 채점 실행 흐름

`engine/grading.py`가 `pipeline/`의 Step을 순서대로 돌립니다.
단계 추가는 Step 추가로 끝나며 호출부는 안 건드립니다(OCP).

```
CollectorStep     장비 SSH 접속 후 raw CLI 출력 수집 (engine/collector.py)
ParserStep        VendorDriver로 커맨드→check_id 역매핑, 벤더별 파서로 구조화
RuleEngineStep    target_state와 대조해 Finding(PASS/FAIL/UNKNOWN) 확정
ScorerStep        Stage 단위 집계
ScoreboardPrintStep  결과 출력
HistoryStep       history 저장 + 직전 회차 대비 diff
AlarmStep         Critical Finding 즉시 통보
AIAnalysisStep    요약·조치권고 생성 (판정 결과는 절대 안 바꿈)
ReportStep        report_latest.md 생성
```

## 폴더 구조

```
main.py                 # 유일한 진입점 — pywebview 창 + Api 합성
web_ui/                 # Material Design 3 프런트엔드 (HTML/CSS/JS)
  vendor/               #   로컬 번들 에셋 (xterm, Material Symbols 아이콘 폰트)
                        #   — 아이콘 폰트는 CDN을 쓰지 않는다. 로딩 중 아이콘이
                        #     리거처 원문("business" 등)으로 보이던 문제 때문.
api/                    # 관심사별 mixin — web_ui가 호출하는 유일한 경계
engine/                 # 비즈니스 로직
  grading.py            #   채점 실행 (UI/스케줄러 공용)
  collector.py          #   장비 접속·수집
  ssh_client.py         #   SSH 접속 인자 조립 (터미널/연결확인 공용)
  device_probe.py       #   장비 연결 확인 + hostname 조회
  scheduler.py          #   정기점검 cron
  project_manager.py    #   labs/<project>/ 랩 정의 CRUD
  profile_manager.py    #   data/<customer>/<profile>/ 워크스페이스 CRUD
pipeline/               # PipelineStep 정의 + 실행기
rule_engine/            # 판정 규칙 → Finding
plugins/vendors/        # VendorDriver (Arista, Cisco)
plugins/parsers/        # (vendor, check_id) 파서 레지스트리
parsers/                # 실제 파싱 구현
core/                   # 경로 해석, 원자적 저장, Finding/Context 스키마
  app_settings.py       #   환경 설정 파일 경로 (전부 앱 전역)
report/                 # 출력 포맷 플러그인 (Markdown/HTML/Excel/PDF/PPTX)
labs/<project>/         # 랩 정의 (stages/target_state/inventory/catalog)
data/<customer>/<profile>/  # 실행 워크스페이스 (runs/logs/reports/exports)
```

## 알려진 제약

- 실제 장비 접속은 사내망(EVE-NG 등)에 도달 가능한 환경에서만 동작합니다.
- VendorDriver는 Arista가 기준 구현이고 Cisco는 부분 구현입니다.
- 남아 있는 구조적 후속 과제는 `ARCHITECTURE.md`의 "Known follow-ups" 참고.
