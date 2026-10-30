# AutoCheck - 네트워크 장비 정기점검 시스템 (v1.0.0)

> **버전**: v1.0.0 | **환경**: Python 3.12+ (Windows 10/11) | **저작권자**: TaeHyeong.Kim | **라이선스**: Proprietary — All Rights Reserved ([LICENSE](./LICENSE))

> **AutoCheck**는 네트워크 장비(Arista EOS, Cisco IOS·IOS-XE·NX-OS)에 SSH 세션으로 점검 명령을 보내 원본 CLI 출력을 남기고, 규칙 기반·AI 로그 분석, 외부 공유용 마스킹, 표준 양식 정기점검 보고서(Excel·PDF), SecureCRT 세션 로그 실시간 감시까지 한 번에 처리하는 Material Design 3 기반 정기점검 도구입니다.
>
> 예전의 채점 파이프라인(목표 상태 대조 → 점수 → 이력)은 UI 진입점이 없고 수집 결과가 파서에 닿지 않아 실제로 동작하지 않았으므로 퇴역했다(IMPROVEMENT_PLAN 결정 D1).

버전 이력은 `CHANGELOG.md`, 개발자 문서는 `docs/`(구조는 `docs/ARCHITECTURE.md`), EXE 사용 안내는 EXE 폴더에 함께 들어 있는 `실행방법.txt`를 참고하세요. 소스로 실행하다 막히면 아래 [문제 해결](#-문제-해결-troubleshooting)을 보세요.

---

## 목차 (Table of Contents)

- [주요 특징 (Key Features)](#-주요-특징-key-features)
- [사전 요구 사항 (Prerequisites)](#-사전-요구-사항-prerequisites)
- [빠른 시작 (Quick Start)](#-빠른-시작-quick-start)
- [사용자 작업 흐름 가이드 (User Workflow Guide)](#-사용자-작업-흐름-가이드-user-workflow-guide)
- [네트워크 구성도 자동 생성 (Auto Topology Generation)](#-네트워크-구성도-자동-생성-auto-topology-generation)
- [실시간 감시 (SecureCRT 세션 로그 Baseline Diff)](#-실시간-감시-securecrt-세션-로그-baseline-diff)
- [주요 설정 파일 (Configuration Files)](#-주요-설정-파일-configuration-files)
- [아키텍처 및 파이프라인 개요 (Architecture & Pipeline Summary)](#-아키텍처-및-파이프라인-개요-architecture--pipeline-summary)
- [프로젝트 디렉터리 구조 (Directory Structure)](#-프로젝트-디렉터리-구조-directory-structure)
- [문제 해결 (Troubleshooting)](#-문제-해결-troubleshooting)
- [알려진 제약 사항 (Known Constraints)](#-알려진-제약-사항-known-constraints)
- [개발자 참고 문서 & 라이선스 (Developer Resources & License)](#-개발자-참고-문서--라이선스-developer-resources--license)

---

## 주요 특징 (Key Features)

- **세션 점검**: 장비 목록의 장비에 SSH로 접속해 명령어 카탈로그의 명령(읽기 전용 정책을 통과한 것만)을 보내고, 원본 CLI 출력을 정기점검 회차의 실행(run)마다 남깁니다. Arista EOS와 Cisco IOS·IOS-XE·NX-OS를 지원합니다(Cisco는 실제 장비로 확인하지 않았습니다).
- **규칙 기반·AI 로그 분석**: 규칙 엔진(맥락 추적 → 억제 → 서명 → 상관)이 원본 로그의 이상 구간을 찾고, 로컬 LLM이나 클라우드 AI(Anthropic Claude·OpenAI·Google Gemini·NVIDIA NIM·xAI Grok·Mistral·DeepSeek·Groq·Perplexity·Upstage Solar, OpenAI 호환 직접 입력)로 더 분석합니다. 클라우드로 보내는 내용은 늘 가리고, 보내기 전에 미리 보고 승인해야 하며, 승인 이력이 남습니다.
- **외부 공유용 마스킹**: 비밀값·IP·MAC·계정·장비명 등을 같은 글자 수의 `*`로 가립니다. 실행하기 전에 미리보기로 확인하고, 실행 뒤에는 결과에 남은 것을 알려 줍니다.
- **정기점검 보고서(Excel·PDF)**: 표준 양식 보고서에 전월 점검값 비교·지원 이력, 네트워크 구성도와 앞 회차 대비 링크 변화, 실시간 감시 이력을 부록으로 담습니다. 고객사마다 점검 항목·표지를 바꾼 템플릿을 쓸 수 있습니다.
- **네트워크 구성도 자동 생성**: 이미 수집하는 LLDP·CDP·인터페이스·MLAG/vPC 출력만으로 3계층 물리 구성도(SVG)를 그리고, 지난 회차와 비교해 새로 생긴·사라진 링크를 보여 줍니다.
- **SecureCRT 세션 로그 실시간 감시**: 작업 중인 세션 로그를 0.3초 간격으로 따라가며 사전 점검 결과(Baseline) 대비 설정 삭제·링크/인접 DOWN·위험 명령을 바로 경고합니다(상단바 알림). 아직 복구되지 않은 장애, 감시가 눈을 잃은 상태(감시 건강)도 보여 줍니다.
- **무인 정기점검과 알림**: Windows 작업 스케줄러가 정한 시각에 창 없이 수집 → 규칙 분석 → 보고서를 만들고, 실패와 실시간 CRITICAL 경고를 Teams·Slack·웹훅·메일로 알립니다.
- **회차 비교**: 두 점검(회차 또는 Run)의 점검 항목·재부팅·OS/S/N·물리 링크 변화를 보여 줍니다.
- **데이터 보호**: 장비 비밀번호·API 키는 Windows DPAPI로 봉인하고, SSH 호스트키가 바뀐 장비에는 로그인하지 않습니다. 설정 파일은 원자적으로 쓰고, 정기점검 삭제는 보관입니다. 설정·장비 목록은 하루 한 번 자동 백업되고, 작업공간 무결성 점검이 어긋난 곳을 알려 줍니다.
- **작업 센터·진단 번들**: 뒤에서 도는 작업의 진행·취소·완료를 상태바 한곳에서 보고, 문제를 알릴 때는 비밀을 지운 진단 zip을 만듭니다.

---

## 사전 요구 사항 (Prerequisites)

- **OS**: Windows 10 / 11 (Microsoft Edge WebView2 Runtime — Windows 10/11 에는 보통 이미 들어 있습니다)
- **Python**: 소스로 실행할 때만 Python 3.12 이상(`engine/topology_svg.py`가 3.12 문법을 씁니다 — 낮은 버전이면 `main.py`가 시작할 때 안내하고 멈춥니다). EXE 배포본은 Python이 들어 있어 따로 설치하지 않습니다.
- **네트워크**: 점검 대상 장비 또는 가상 실습 환경(EVE-NG 등)에 SSH로 닿는 환경
- **의존성 패키지**: `requirements.txt`

---

## 빠른 시작 (Quick Start)

### 1. 소스로 실행

```bash
# 필수 의존성 라이브러리 설치
pip install -r requirements.txt

# GUI 앱 실행 (유일한 진입점)
python main.py
```

> **의존성 파일**: 빠졌거나 버전이 낮은 패키지가 있으면 `main.py`가 무엇이 문제인지와 설치 명령을 알려 주고 멈춘다(실행할 때마다 자동 설치하려면 환경변수 `AUTOCHECK_AUTO_INSTALL=1`). 테스트가 통과한 정확한 버전 조합은 `requirements.lock`, EXE 빌드 도구는 `requirements-build.txt`, 테스트 도구는 `requirements-dev.txt`에 있다.

### 2. EXE 배포본

`python build_exe.py`가 `dist/AutoCheck/` 폴더를 만듭니다(빌드 도구: `pip install -r requirements-build.txt`). 사용자는 그 폴더째 받아 `AutoCheck.exe`를 실행합니다 — 폴더의 `실행방법.txt`가 EXE 사용자용 안내입니다.

### 3. 데이터 폴더

점검 로그·보고서·설정은 `Documents\AutoCheck`(사용자 데이터 폴더)에 쌓입니다. 다른 곳을 쓰려면 환경변수 `AUTOCHECK_HOME`에 폴더를 지정하거나, 앱 폴더에 `autocheck_data` 폴더(또는 `portable.txt` 파일)를 두면 그 폴더를 씁니다(포터블). 폴더 안의 배치와 백업·업그레이드는 [docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md)에 있습니다.

### 4. 무인 정기점검

워크스페이스의 '무인 정기점검' 카드에서 일정을 저장하면 Windows 작업 스케줄러에 작업이 등록되고, 그 시각에 창 없이 `AutoCheck.exe --scheduled <일정 id>`(소스: `python main.py --scheduled <일정 id> --data-root <데이터 폴더>`)가 돕니다. 직접 실행할 일은 없습니다.

---

## 사용자 작업 흐름 가이드 (User Workflow Guide)

사이드바의 14개 메뉴는 업무 순서대로 **준비 → 실행 → 결과 → 기타** 네 묶음입니다.

### [1] 준비 (Preparation)
* **1. 워크스페이스**: 고객사를 추가하고 정기점검 회차(프로파일)를 만들거나 고릅니다. 회차의 실행(Run) 이력과 보고서·내보내기·로그를 한곳에서 봅니다.
  * *팁*: 같은 고객사의 두 번째 회차부터는 직전 회차의 장비 목록을 자동으로 물려받습니다.
  * *보관함*: 정기점검을 삭제하면 지우지 않고 보관합니다. 고객사/정기점검 창 머리의 '보관함'에서 되돌리거나 영구 삭제합니다(정기점검 이름을 입력해야 지웁니다).
  * *다른 PC 로 옮기기*: 고객사/정기점검 창의 정기점검 카드에서 '내보내기'를 누르면 그 정기점검의 정의(프로젝트 정보·장비 목록 등)를 zip 으로 저장합니다 — 장비 비밀번호를 담을지 묻습니다(담으면 zip 안이 평문입니다). 새 PC 에서는 창 머리의 '가져오기'로 들이고, 담아 온 비밀번호는 들어오는 즉시 그 PC 의 봉인으로 다시 감쌉니다. 같은 고객사에 같은 이름이 있으면 이름 뒤에 '(가져옴)'을 붙입니다. 점검 로그·보고서는 워크스페이스 탭의 내보내기로 옮깁니다.
  * *회차 비교*: '회차 비교' 카드에서 두 점검(회차 또는 Run)을 고르면 나빠진·좋아진 점검 항목, 재부팅, OS·S/N 변화, 새로 생긴·사라진 링크를 보여 줍니다. Run 이력의 '비교' 버튼은 그 Run 을 바로 앞 점검과 비교합니다.
  * *무인 정기점검*: '무인 정기점검' 카드에서 매일·매주(요일)·매월(날짜)·시각과 보고서 형식을 정해 저장하면 Windows 작업 스케줄러에 등록되어, 그 시각에 창 없이 수집 → 규칙 분석 → 보고서를 만듭니다. 결과는 카드의 실행 이력에, 실패는 사이드바 '워크스페이스'의 빨간 배지로 알립니다.
  * *작업공간 점검·백업*: '작업공간 점검·백업' 카드에서 '무결성 점검'을 누르면 목록과 폴더가 어긋난 곳(이름을 바꾼 폴더, 연결되지 않은 점검 데이터), 읽을 수 없는 설정·상태 파일, 다른 PC 에서 옮겨 와 풀 수 없는 비밀번호, 남은 임시 파일을 할 일과 함께 보여 줍니다(고치지는 않습니다). 설정·장비 목록·명령 카탈로그·보고서 기록은 앱을 켤 때 하루 한 번 `backups\`에 zip 으로 남습니다(최근 10개 — 점검 로그·보고서 파일은 담지 않습니다). 되살릴 때는 zip 에서 필요한 파일을 같은 자리로 꺼내세요.
* **2. 장비 목록**: 관리 IP, 포트, 계정을 입력하고 점검할 장비를 켭니다. 점검은 여기서 켠 장비에만 접속합니다.
  * IP/계정을 고치면 바로 접속을 확인하고, 성공하면 장비가 알려준 `hostname`으로 이름을 갱신합니다(바꾸기 전 이름은 별칭으로 남깁니다).
  * *SSH 호스트키*: 처음 접속한 장비의 키를 기억하고, 키가 바뀐 장비에는 로그인하지 않습니다(고객사마다 따로 기억). 장비를 바꾸거나 다시 설치해 키가 바뀐 것이 맞으면 'SSH 호스트키'에서 지문을 확인한 뒤 '다시 신뢰'를 누르세요.
  * *벤더*: Arista EOS·Cisco IOS·Cisco IOS-XE·Cisco NX-OS 중에서 고릅니다. 접속 방식과 점검 명령이 벤더에 맞춰 바뀝니다(카탈로그의 체크ID 로 같은 목적의 Cisco 명령을 고릅니다). 로그인 뒤 `>` 에서 시작하는 Cisco 장비는 'enable' 열(또는 기본값의 '기본 enable 비밀번호')에 enable 비밀번호를 넣어야 점검합니다.
  * *별칭*: 장비가 다른 이름(장비가 말하는 호스트명·FQDN, SecureCRT 파일명, 바꾸기 전 이름)으로도 불리면 '별칭' 칸에 쉼표로 적습니다. 구성도의 LLDP 이웃, 실시간 감시의 로그 파일, 보고서가 같은 장비로 맞춥니다.
* **3. 명령어 카탈로그**: 점검 때 장비에 보낼 CLI 명령을 메모장처럼 적습니다 — 한 줄에 명령 하나, 명령 다음 줄에 `!`(장비에 입력하는 모양 그대로, 이 순서대로 보냅니다). 고치면 자동으로 저장되고 '저장'(Ctrl+S)은 빠진 `!` 를 채워 줄을 정리합니다. 읽기 전용 명령만 받습니다 — 설정을 바꿀 수 있는 명령이 있으면 저장하지 않고 그 줄을 알려 주며, 한글이 섞인 명령(한/영 전환 실수)도 알려 줍니다. 오른쪽 '기본 카탈로그 명령'에서 빠진 기본 명령을 눌러 더합니다.
  * *내보내기·불러오기*: `.txt`(같은 모양)로 저장하고 불러옵니다(예전에 내보낸 Excel 도 불러옵니다). '폴더 열기'는 그 파일을 두는 정기점검의 `commands` 폴더를 엽니다.

### [2] 실행 (Execution)
* **4. 세션 터미널**: 장비 목록의 장비로 SSH 터미널을 여러 개 띄워 다룹니다. 접속하면 장비 이름을 프롬프트의 호스트명으로 바로 맞춥니다(다른 장비와 이름이 겹치면 바꾸지 않고 알립니다 — 고친 뒤 '이름 동기화'). '세션 점검'은 카탈로그 명령을 보내 원본 로그를 이 회차의 실행(run)에 남깁니다.
* **5. 실시간 감시 (선택)**: SecureCRT로 작업하는 동안 Baseline 대비 변경을 실시간으로 감시합니다(아래 '실시간 감시' 절).
* **6. 점검 로그**: 장비에서 수집한 원본 CLI 출력을 봅니다. 로그는 5,000줄씩 쪽을 나눠 열고, 찾기는 파일 전체에서 합니다(정규식·대소문자 구분 선택). 로그 글은 끌어서(두 번·세 번 눌러도) 고르면 바로 클립보드에 복사됩니다 — 원본로그분석·마스킹 결과, 전체 로그, 실시간 감시의 로그 줄도 같습니다.

### [3] 결과 (Results)
* **7. 원본로그분석**: 원본 로그를 훑어 에러 구간을 찾고 이상 징후를 분석합니다 — 규칙 기반, 로컬 AI, 클라우드 AI. '점검 회차'에서 어느 점검의 로그를 분석할지 고릅니다(기본: 가장 최근 점검 — 원본로그분석·마스킹·보고서가 같은 선택을 이어 씁니다). 클라우드 AI 로 보낼 때는 승인 창에서 보낼 내용(가린 뒤의 글)을 확인하고 승인해야 합니다.
* **8. 마스킹**: 외부 공유용으로 로그의 민감정보를 가립니다('점검 회차'로 어느 점검의 로그인지 고릅니다). '미리보기'로 한 파일에서 가릴 값과 그대로 남는 것을 먼저 확인하고, 실행 뒤에는 결과에 남은 것(비밀·놓친 값·고르지 않은 값·장비 이름)을 알려 줍니다.
* **9. 네트워크 구성도**: 수집된 로그로 물리 구성도를 그립니다(아래 '네트워크 구성도' 절).
* **10. 대시보드**: 전체 Health, 로그 수집률, 비정상 로그 비율, 인시던트(중복 제거) 수와 장비별 수집 커버리지·Health Score, 에러 유형 Top 10, 비정상이 많은 장비를 보여 줍니다.
* **11. 보고서**: 원본 로그와 장비 목록으로 정기점검 보고서(Excel·PDF)를 만들어 이 회차의 `reports/` 폴더에 저장합니다. '점검 회차'를 고르면 그 점검의 로그로만 만들고, 기본값은 장비마다 가장 최근 로그입니다(장비 몇 대만 다시 점검한 회차를 합쳐 씁니다). 장비 판정을 기억해 두어 두 번째부터는 바로 열립니다. PDF 끝에는 구성도와 앞 회차 대비 링크 변화, 실시간 감시를 쓴 회차면 경고와 처리 결과를 담은 '실시간 감시 이력' 부록(Excel 은 시트)이 붙습니다.
  * *고객사 템플릿*: '고객사 템플릿 만들기'로 `data\<고객사>\report_template.yaml` 을 만들어 그 고객사만의 점검 항목·표지를 더하고 고치고 뺍니다. 항목에 `vendors` 를 적으면 그 벤더 장비에서만 점검합니다.

### [4] 기타 (Settings & Logs)
* **12. 지식베이스**: 프로젝트별 지식 문서(Markdown) — 트러블슈팅 노트, 표준 절차 등을 적어 둡니다.
* **13. 환경 설정**: 모든 고객사·회차에 공통으로 적용됩니다.
  * *AI 제공자 순서*: 끌어서 순서를 정합니다. 1순위가 주 분석기이고, 쓸 수 없으면 다음 순서로 넘어갑니다(`ai_settings.yaml`). 클라우드 API 키·모델과 로컬 AI 주소도 여기서 넣습니다.
  * *SSH 공통 접속 인자*와 터미널 동작.
  * *알림 채널*: Teams·Slack·웹훅(사내 메신저)·메일(SMTP)을 등록하면 실시간 감시 CRITICAL 경고, 무인 정기점검 실패, 무인 점검 수집 실패를 보냅니다. '시험 보내기'로 연결을 확인하세요.
* **14. 전체 로그**: 프로그램의 모든 동작·오류 로그를 레벨 필터(`INFO`/`WARN`/`ERROR`/`DEBUG`)로 보고, 세션 로그(.txt)로 내보냅니다.
  * *진단 번들*: 문제를 알릴 때 '진단 번들'로 버전·환경·최근 앱 로그·설정을 zip 하나로 만듭니다. 비밀은 늘 지우고, '이름·주소도 가리기'(기본 켬)로 장비·고객사 이름과 IP·MAC·계정·도메인도 가립니다.

### 상단바와 상태바
* **실시간 경고 알림(상단바의 종 버튼)**: 실시간 감시 경고가 오면 읽지 않은 수가 배지로 붙고, 버튼이 가장 심각한 등급의 색으로 바뀝니다. 누르면 알림 창에서 경고를 확인하고 해결·무시합니다.
* **작업 센터(상태바의 '작업')**: 점검·분석·보고서·내보내기처럼 뒤에서 도는 작업의 진행률과 이번 실행에서 끝난 작업(완료·취소됨·오류)을 한곳에서 봅니다. 세션 점검과 분석(규칙·로컬 AI·클라우드 AI)은 여기서 '취소'로 멈출 수 있습니다 — 그때까지의 결과는 남습니다.

---

## 네트워크 구성도 자동 생성 (Auto Topology Generation)

사이드바 **네트워크 구성도** 탭(v0.5.9 신규)은 장비 목록과 수집된 점검 로그에서 **물리 구성도를 자동으로 생성 및 시각화**합니다. 별도의 추가 수집 없이 정기점검 시 수집되는 출력만으로 토폴로지를 복원합니다.

```
[수집 로그: lldp neighbors / interfaces status / interfaces description / show mlag
            (Cisco: cdp·lldp neighbors detail / etherchannel·port-channel summary / show vpc)]
       │
[engine/topology_builder.py] ──► 노드·링크·Port-Channel·MLAG 이중화 구조 파싱
       │
[engine/topology_layout.py]  ──► 3계층(Core/Agg/Access) 자동 배치 + 사용자 수동 좌표 영속화
       │
[engine/topology_svg.py]     ──► SVG 생성 (관례적 기하 도형, 곡선 우회 라우팅, 라벨 충돌 방지)
```

### 주요 기능 및 특징
- **수집 로그 기반 자동 복원**:
  - `show lldp neighbors`(Cisco: `show cdp neighbors detail`·`show lldp neighbors detail`): 이웃 장비 및 물리 포트 연결 관계 복원
  - `show interfaces status`: 링크 상태(UP/DOWN) 및 Port-Channel 소속 (`in Po2048`) 추적(Cisco 의 Port-Channel 소속은 `show etherchannel summary`·NX-OS `show port-channel summary`)
  - `show interfaces description`: 인터페이스 설명 라벨 (`core_agg_mlag`)
  - `show mlag`(NX-OS: `show vpc`): peer-link Po 기반 이중화 쌍 그룹핑
- **표준 네트워크 다이어그램 심볼**: 저작권 이슈가 없는 관례적 기하 도형 적용 (L2 스위치: 나란한 화살표 4개, L3 스위치: + 라우터 화살표, 라우터: 원+방사형, 방화벽: 벽돌, 미등록: 점선+?)
- **지난 회차와 비교**: 새 링크는 파란 후광, 지난 회차에만 있던 링크는 빨간 점선과 '사라짐'. 양쪽 장비 모두 이웃 정보가 없던 회차의 링크는 '확인 불가'로 따로 셉니다. 같은 그림이 정기점검 PDF 의 부록으로도 들어갑니다.
- **3계층 구조 & 노드 위치 영속화**: Core / Aggregation / Access 3계층 자동 분류 및 사용자가 마우스로 노드를 끌어 옮긴 수동 위치 좌표를 프로파일별로 영속 저장
- **명확한 시각화 & 색약 대응**:
  - MLAG 이중화 쌍: 점선 상자 래핑
  - Port-Channel 묶음: 굵은 선 + `Po2048 x2` 묶음 표시 및 설명 라벨
  - 장애 상태: DOWN 링크는 빨간 선 + ✕ 마커 (색약 및 흑백 인쇄 대응), 일부 DOWN은 반쪽 원, 미관측은 회색
  - 실시간 감시가 도는 중이면 감시가 본 링크 다운과 장비별 미해결 경고 수가 함께 얹힙니다.
- **충돌 방지 알고리즘**:
  - **우회 곡선 라우팅**: 직선이 다른 노드를 관통할 경우 2차 베지어 곡선으로 우회 렌더링
  - **라벨 충돌 회피**: 선 상의 비어있는 후보 위치를 스마트 탐색하고 글자 배경 테두리(paint-order)로 시인성 확보
  - **다양한 토폴로지 검증**: 스파인-리프, 링, 스타, 체인, 풀메시, FQDN/Cisco 명명 등 21가지 예제 토폴로지 자동 기하 검증 완료

---

## 실시간 감시 (SecureCRT 세션 로그 Baseline Diff)

사이드바 **실시간 감시** 탭은 SecureCRT로 장비를 만지는 동안 사전 점검 결과와 달라지는 것을 즉시 알려줍니다.
점검 로그를 나중에 분석하는 기존 흐름(`원본로그분석`)과 달리, 작업 중에 사고를 잡는 것이 목적입니다.

```
[SecureCRT 터미널 작업]
      │ 세션 로그 append
[Documents/AutoCheck/CRTlog/*.txt · *.log]
      │ 바이트 오프셋 감시(0.3초) — 새로 덧붙여진 부분만
[core/crt_stream_watcher.py]
      │ 파일 → 장비 판정 (engine/stream_device_matcher.py)
[engine/baseline_diff_engine.py] ◄── [engine/baseline_store.py] 00_orignal_log 기준 스냅샷
      │
[engine/realtime_monitor.py] ──► 화면 3분할 (로그 / 체크리스트 / 원인 분석) + 상단바 경고 알림
```

### 화면 구성
- **좌측**: 장비별 실시간 CLI 캡처. `탭 보기`는 한 장비씩, `분할 보기`는 장비 박스를 위아래로 쌓습니다.
  각 장비 박스는 스크롤되지 않고 최신 줄만 보여주며, 박스 목록 전체만 스크롤됩니다.
- **우측 상단**: 실시간 오류 분석. 단순 나열이 아니라 인과로 묶습니다 — 설정 변경 명령 뒤에 DOWN이
  따라오면 "작업 명령이 통신 단절을 유발"로, 변경 없이 DOWN만 나면 "대향 장비/물리 경로 의심"으로 분류합니다.
- **우측 하단**: 장비 × 7개 항목(VLAN / 인터페이스 / 정적 라우팅 / 라우팅 인접 / 링크 / STP·MLAG / 위험 명령)
  체크리스트. 이상 항목이 위로 정렬되고, 그룹을 클릭하면 세부 경고 이력이 열립니다.
- **경고 알림**: 새 경고는 상단바의 종 버튼으로 옵니다 — 읽지 않은 수 배지와 가장 심각한 등급의 색. 누르면 알림 창에서
  경고를 보고 '해결'·'무시'합니다.
- 좌우·상하 **구분선은 드래그로 비율 조절**되며, 그 값과 보기 모드·선택 장비는
  `config/realtime_watch.yaml`에 저장돼 프로그램을 다시 켜도 유지됩니다.
- **프로그램 실행 시 자동 시작** 체크박스를 켜면 앱을 띄우는 즉시 감시가 시작됩니다.
- **우클릭 컨텍스트 메뉴**: 로그 박스, 체크리스트, 분석 결과 항목을 우클릭하여 특정 장비나 규칙의 경고를 숨김 처리할 수 있습니다.
- **고정 카드 및 표시 설정**: 중요한 항목(예: 특정 장비의 특정 장애)을 상단에 고정 카드로 띄울 수 있으며, 설정 모달을 통해 관리할 수 있습니다.
- **열린 장애**: 아직 복구를 보지 못한 링크 다운·라우팅 인접 끊김·MLAG 피어 끊김·설정 삭제가 상단 줄에 경과 시간과 함께 보입니다. 복구 줄이 오면 저절로 사라지고, '해제'로 직접 닫을 수 있습니다. 프로그램을 다시 켜도 이어집니다.
- **감시 건강**: 상태 폴링 인증 실패·호스트키 바뀜·세션 로그 없음·로그 기록 멈춤(20분)처럼 감시가 눈을 잃었을 때만 상단에 배너가 뜹니다. '감시 건강' 버튼은 장비마다 마지막 기록·syslog 마지막 수신·명령 마지막 입력·상태 폴링 결과를 보여 줍니다.
- **규칙 실험실**(플라스크 아이콘): 세션 로그 줄을 붙여넣으면 줄마다 판정 경로(명령/출력, 억제, 서명·키워드, diff 경고·복구)와 실시간 목록에 올라가는지를 보여 주고, `config/log_rules.json` 의 실수(깨진 정규식, 모든 줄에 맞는 패턴, 겹친 id 등)를 점검합니다.

### 감지 항목
| 구분 | 감지 대상 | 판정 |
|---|---|---|
| 설정 삭제 | `no vlan` · `no ip route` · `no interface` · `no neighbor` · `no router bgp` | Baseline에 있던 대상이면 CRITICAL |
| 인터페이스 차단 | `shutdown` (config 문맥에서 대상 인터페이스 추적) | Baseline 등록 인터페이스면 CRITICAL |
| 위험 운영 명령 | `reload` · `write erase` · `erase startup-config` 등 | CRITICAL |
| 상태 변화 | `BGP/OSPF-5-ADJCHANGE` · `LINEPROTO-5-UPDOWN` · `MLAG` · `STP` | DOWN이면 CRITICAL, 복구면 WARNING |

### 로그 파일 ↔ 장비 매칭
SecureCRT는 세션 로그 파일명을 장비명이 아니라 **접속 IP**로 남기는 경우가 많고(`192.168.205.101_20260803_152204.txt`),
기본 파일명이 `session.log`인 환경도 있습니다. 그래서 다음 순서로 판정합니다:

1. 파일명 토큰이 장비 목록의 장비명·별칭과 일치
2. 파일명에 든 IP가 장비 목록의 관리 IP와 일치
3. 로그 내용의 `! device: X` 헤더 (`show running-config` 출력에 항상 있음)
4. 로그 내용의 프롬프트 `X#` / `X>`

그래도 실패하면 **파일 진단** 버튼으로 파일별 매칭 근거를 확인할 수 있습니다. 감시 중 식별하지 못한 파일이
있으면 **파일 진단** 버튼에 그 개수가 표시되고, 감시 대상 장비에 따라갈 세션 로그가 없으면 감시 건강 배너가 알립니다.

> **참고**: 감시를 시작하기 전부터 파일에 있던 내용은 화면에만 흐리게 채워지고(`--:--:--` 표시) 경고를
> 발생시키지 않습니다. 어제 실행한 명령이 오늘 감시를 켠 순간 CRITICAL로 튀어나오면 알림을 신뢰할 수
> 없기 때문입니다. 감시 중에 새로 열린 세션 로그는 첫 줄부터 판정합니다.

---

## 주요 설정 파일 (Configuration Files)

설정은 사용자 데이터 폴더(`Documents\AutoCheck`)에 있고, 거의 모두 화면에서 고칩니다. 앱 폴더의 같은 이름 파일은 첫 실행 때 복사되는 기본값입니다.

- `ai_config.yaml`: AI 분석 제공자(클라우드 10종 + OpenAI 호환 직접 입력, 로컬 LLM)의 API 키·모델·엔드포인트와 `local_batching` 설정. 키는 봉인되어 있다. 사용자 데이터 폴더에만 있다 — 설정 화면이 만들고, EXE에는 담기지 않는다(빌드가 비밀 검사로 막는다)
- `ai_settings.yaml`: AI 제공자 우선순위(`order:` — 규칙 기반·로컬 AI·클라우드 등). 1순위가 주 분석기이고, 쓸 수 없으면 다음으로 넘어간다. 환경 설정의 'AI 제공자 순서'에서 끌어서 바꾼다
- `config/alarm.yaml`: 알림 채널(Teams·Slack·웹훅·메일)과 가림 수준·중복 억제·시간당 한도·보낼 사건. 웹훅 주소·메일 비밀번호는 봉인되어 있다 — 화면(환경 설정)에서 고친다
- `schedules.yaml` / `scheduled_runs.jsonl`: 무인 정기점검 일정(프로파일마다 하나)과 실행 이력. 화면에서 고친다 — 작업 스케줄러의 작업은 일정 id(`\AutoCheck\scheduled-<id>`)로 이 파일을 가리킨다
- `ai_transmissions.jsonl`: 외부 AI 전송 승인 이력(설정 아님). 승인할 때마다 시각·사용자·받는 곳·파일·요청 수·글자 수·내용의 SHA-256 과 결과를 한 줄씩 남긴다 — 보낸 내용과 키는 남기지 않는다
- `connection.yaml`: SSH 접속 타임아웃(`ssh.connect_timeout_sec`), 재시도 횟수, 사전점검 대상. 장비 계정은 장비 목록 화면에서 정한다 — 비어 있으면 접속하지 않는다(예전의 admin/admin 대입 없음)
- `ssh_host_keys.json`: 고객사마다 기억한 장비의 SSH 호스트키(장비 목록의 'SSH 호스트키'에서 관리)
- `config/realtime_watch.yaml`: 실시간 감시 자동 시작 여부, 화면 구분선 비율·보기 모드·선택 장비·필터 및 고정 카드 영속화 설정
- `config/log_rules.json`: 로그 분석과 실시간 감시가 함께 쓰는 판정 규칙 — 서명 패턴, L2/L3/Overlay 규칙, 연쇄 상관규칙, 상태 취소 추적 패턴
- `config/inspection_report_template.yaml`: 정기점검 보고서의 점검 항목·표지(고객사 템플릿 `data\<고객사>\report_template.yaml` 이 그 위에 얹힌다)

---

## 아키텍처 및 파이프라인 개요 (Architecture & Pipeline Summary)

점검은 한 경로다(예전 채점 파이프라인은 결정 D1 에 따라 퇴역):

```
[세션 점검]        카탈로그 명령(읽기 전용 정책 통과분) → 원본 로그 runs/<run_id>/raw/
  api/terminal_inspection_api.py, core/command_policy.py, engine/log_storage.py
        │
[로그 분석]        규칙 엔진(맥락 추적 → 억제 → 서명 → 상관) + 선택적 AI 분석
  engine/log_analysis.py, engine/log_rule_engine.py(config/log_rules.json), ai_analysis/
        │
[마스킹]           외부 공유용 비밀값·주소 가리기(클라우드 AI 전송 시 강제 + 미리보기 승인)
  engine/log_masking.py
        │
[정기점검 보고서]   표준 양식 Excel·PDF (전월 비교·지원이력·부록)
  engine/inspection_report_builder.py, report/inspection_excel.py, report/inspection_pdf.py
```

무인 정기점검(`main.py --scheduled`)은 같은 경로를 창 없이 한 번 돈다(`engine/scheduled_inspection.py` — 수집은 `engine/collector.py`).
실시간 감시는 SecureCRT 세션 로그를 따라가며 Baseline(직전 점검 로그) 대비 변경을 판정한다
(`core/crt_stream_watcher.py` → `engine/baseline_diff_engine.py` + `engine/realtime_rule_stream.py` → `engine/realtime_monitor.py`).

> **상세 개발자 안내**: 계층과 의존 방향, 5대 워크스페이스 매니저 구조, API 계층 믹스인 설계 등은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 문서를 참고하세요.

---

## 프로젝트 디렉터리 구조 (Directory Structure)

앱(소스) 폴더:

```
main.py                 # 앱 유일 진입점 (pywebview 창 + Api 믹스인 합성, --scheduled = 무인 점검)
web_ui/                 # Material Design 3 프런트엔드 (HTML/CSS/JS 및 에셋)
api/                    # GUI-Engine 간 비즈니스 API 믹스인 레이어
engine/                 # 워크스페이스·run·로그 분석·실시간 감시·보고서 조립 핵심 로직
ai_analysis/            # AI 로그 분석(제공자 라우팅·분할 전송·전송 기록)
parsers/                # 상태 폴링·구성도가 쓰는 show 출력 파서
report/                 # 정기점검 Excel·PDF 렌더러, 점검 판정 상태값, 원본 로그 파서(textfsm)
core/                   # 공통 I/O, 원자적 쓰기(atomic_io), 경로 해석, 명령 정책, 로깅, 비밀 봉인
config/                 # 번들 기본 설정(첫 실행 때 사용자 데이터 폴더로 복사)
tools/                  # 개발 도구(벤치마크, 번들 비밀 검사, 골든 픽스처 승격)
tests/                  # pytest(+ node 로 도는 JS 테스트)
docs/                   # 개발자 문서(구조·가이드·개선 계획·데이터 배치 DATA_LAYOUT, history/ 는 끝난 계획)
```

사용자 데이터 폴더(`Documents\AutoCheck`):

```
config/                 # 고객사 목록·활성 프로파일·알림·감시 화면 설정 등
labs/<project_id>/      # 정기점검 프로파일의 정의(project_meta.yaml·장비 목록·명령 카탈로그) — 이름은 퇴역한
                        # 채점 파이프라인 시절의 것이다(레거시 이름, 지금도 쓰는 폴더)
data/<고객사>/<프로파일>/  # 회차의 실행 워크스페이스(runs/<run_id>/raw·masked·…, reports/, realtime_state.json)
CRTlog/                 # SecureCRT 세션 로그(실시간 감시가 따라간다)
backups/                # 작업공간 자동 백업 zip(최근 10개)
logs/                   # 앱 로그
```

앱 폴더에 예전 버전의 `labs/`가 있으면 데이터 업그레이드가 한 번 읽어 사용자 데이터 폴더로 옮깁니다(원본은 그대로 둡니다). 앱이 평소에 읽는 것은 사용자 데이터 폴더의 `labs/`입니다.

---

## 문제 해결 (Troubleshooting)

터미널에 찍힌 오류 메시지를 그대로 캡처하면 원인 확인이 빠릅니다. 앱이 뜬다면 '전체 로그' 탭의 **진단 번들**을 붙이세요(비밀을 지운 zip).

- `[안내] 설치되지 않은 패키지: ...` / `[안내] 버전이 낮은 패키지: ...`
  → `python -m pip install -r requirements.txt` 를 실행한 뒤 다시 실행합니다. 실행할 때마다 자동 설치하려면 환경변수 `AUTOCHECK_AUTO_INSTALL=1`.
- `[!] AutoCheck 는 Python 3.12 이상이 필요합니다(...)`
  → python.org 에서 새 버전을 설치합니다("Add to PATH" 체크).
- `'python'은(는) 내부 또는 외부 명령... 아닙니다`
  → Python 이 설치되지 않았거나 PATH 에 없습니다.
- `UnicodeDecodeError: 'cp949' codec can't decode ...`
  → 한글 Windows 인코딩 문제입니다. 앱의 파일 읽기·쓰기는 UTF-8 로 고정되어 있으니, 직접 추가한 코드의 `open()`에 `encoding="utf-8"`을 넣으세요.
- 창이 뜨지 않음(pywebview)
  → Microsoft Edge WebView2 Runtime 이 있는지 확인합니다.
- 다른 PC 에서 옮겨 온 데이터의 비밀번호·API 키가 비어 있음
  → 봉인된 비밀은 같은 PC·같은 Windows 사용자에서만 풀립니다. 다시 입력하세요(워크스페이스 '무결성 점검'이 어느 장비 목록인지 알려 줍니다).

---

## 알려진 제약 사항 (Known Constraints)

- **네트워크 접근성**: 실제 장비 점검은 대상 네트워크 장비 또는 EVE-NG 등 가상 실습망에 SSH 접근이 가능한 환경에서 동작합니다.
- **벤더 지원 범위**: Arista EOS 와 Cisco IOS·IOS-XE·NX-OS 를 점검합니다(그 밖의 벤더는 접속하지 않습니다). Cisco 는 실제 장비로 확인하지 않았습니다 — 명령과 출력 해석은 Cisco 명령 레퍼런스 기준이라, 처음 점검한 결과를 확인하세요. 규칙 판정(`config/log_rules.json`)의 문구와 실시간 감시는 EOS 기준이고 Cisco syslog 는 일부만 인식합니다. Cisco 장비의 enable 이 실패하면(비밀번호 없음·틀림) 그 장비는 점검하지 않습니다(IMPROVEMENT_PLAN 4-5).
- **클라우드 AI 전송**: 보내기 전에 승인 창에서 내용을 확인하고 승인해야 합니다(이 PC 안의 로컬 AI 는 예외). 비밀값·IP·MAC·계정·도메인과 장비명(이 로그의 장비, 설명·LLDP 에 이웃으로 적힌 장비 목록의 장비)은 늘 가리지만, 장비 목록에 없는 이름(통신사·다른 고객사 장비 등)은 가리지 못할 수 있습니다 — 승인 창의 '내용 보기'로 확인하세요. 고객사의 반출 정책은 따로 확인해야 합니다.
- **무인 정기점검**: PC 가 켜져 있고 일정을 저장한 Windows 사용자가 로그인해 있어야 돕니다(화면 잠금은 괜찮음 — 비밀번호를 작업에 저장하지 않고, 봉인된 장비 비밀번호를 그 사용자만 풀 수 있어서입니다). 꺼져 있어 놓친 일정은 다음에 켜졌을 때 한 번 돌고, 앞 실행이 안 끝났으면 새 실행은 건너뜁니다. 같은 프로파일의 점검이 이미 진행 중이면 '건너뜀'으로 기록됩니다. 앱을 새 버전으로 바꾼 뒤에는 한 번 열어야(데이터 업그레이드) 무인 점검이 돕니다. 프로파일을 보관(삭제)하면 그 일정과 작업도 지워지고, 복원해도 일정은 다시 저장해야 합니다. 데이터 폴더를 옮기면 일정을 다시 저장하세요(작업이 등록할 때의 폴더를 가리킵니다).
- **알림 채널**: Teams·Slack 은 외부 서비스입니다. 보내는 내용은 가리지만(기본: 비밀값·IP·MAC·계정) 장비명·고객사명은 남습니다 — 고객사의 반출 정책을 확인하고, 필요하면 가림 수준을 '장비명까지'로 바꾸세요. 실시간 경고 알림은 앱이 켜져 있을 때만, 무인 점검 알림은 작업 스케줄러가 점검을 돌릴 때 갑니다.
- **SSH 호스트키**: 처음 접속할 때 받은 키를 그대로 믿습니다(TOFU) — 첫 접속이 이미 가로채인 경우는 막지 못합니다. 장비를 교체·재설치하면 '호스트키 바뀜'으로 그 장비 점검이 멈추니, 지문을 확인하고 '다시 신뢰'하세요(무인 점검도 그때까지 그 장비를 수집하지 않습니다).
- **비밀값 보관**: 장비 비밀번호·개인키·API 키는 Windows DPAPI 로 봉인되어 이 PC 의 이 Windows 사용자만 풀 수 있습니다. 데이터 폴더(`Documents\AutoCheck`)를 다른 PC 로 그대로 복사하면 비밀번호·API 키를 다시 입력해야 합니다 — 옮길 때는 고객사/정기점검 창의 '내보내기'에서 비밀번호를 담아 내보내고 새 PC 에서 '가져오기'하세요(평문 zip 이니 취급 주의).
- **작업공간 백업**: 이 PC 의 데이터 폴더 안(`backups\`)에 남습니다 — 디스크 고장이나 PC 분실에는 대비하지 못하니 중요한 데이터는 따로 복사하세요. 봉인된 비밀번호는 봉인된 채로 담겨 다른 PC 에서는 풀리지 않습니다.
- **후속 과제**: 구조적 리팩토링 항목 및 기술 부채 관련사항은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)의 *Known follow-ups* 섹션을 참고하세요.

---

## 개발자 참고 문서 (Developer Resources)

- **버전 변경 이력**: [CHANGELOG.md](CHANGELOG.md)
- **시스템 아키텍처 상세**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **개발자 작업 가이드**(규칙·테스트·빌드): [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- **개선 계획(V0.5.9 → V1.0)**: [docs/IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md)
- **데이터 배치·백업·업그레이드**: [docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md)
- **끝난 계획(성능 최적화)**: [docs/history/OPTIMIZATION_PLAN.md](docs/history/OPTIMIZATION_PLAN.md)

---

## License

Copyright (c) 2026 TaeHyeong.Kim. **All Rights Reserved.**

- **No Commercial Use**: Commercial use of any kind is strictly prohibited.
- **No Modification**: Modifying, adapting, or creating derivative works is prohibited.
- **No Distribution / Reproduction**: Copying or redistributing this software or source code is prohibited.

See the [LICENSE](./LICENSE) file for details.
