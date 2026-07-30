# AutoCheck — 네트워크 장비 자동 점검/채점 시스템 (v0.0.036)

> **AutoCheck**는 라우터, 스위치 등 네트워크 장비에 SSH로 자동 접속하여 원본 CLI 출력을 수집하고, 멀티 벤더 파싱, 목표 상태 대조, AI 기반 분석 및 다각도 리포트 생성까지 한 번에 처리하는 Material Design 3 기반 통합 점검/채점 솔루션입니다.

버전 이력은 `CHANGELOG.md`, 개발자 구조 설명은 `ARCHITECTURE.md`, 문제 해결은 `실행방법.txt`를 참고하세요.

---

## 📌 주요 특징 (Key Features)

- 🎨 **Material Design 3 웹 UI**: pywebview 기반의 직관적인 사용자 인터페이스 및 로컬 터미널 내장
- ⚡ **자동 연결 검증 & Hostname 자동 동기화**: IP/계정 설정 즉시 실시간 접속 확인 및 장비 이름 자동 갱신
- 🔄 **회차별 장비 목록 자동 승계**: 동일 고객사의 이전 정기점검 회차 장비/IP/계정 정보 자동 물려받기
- 🔍 **이상 탐지 & 데이터 마스킹**: 원본 로그 분석, 크리티컬 알람 및 외부 공유용 개인정보/보안 데이터 마스킹
- 🤖 **vEOS-lab & Local AI 최적화**: Arista vEOS 가상 플랫폼 특성 감안 프롬프트, 사전 구조화 경량화 패킷 전송 및 Cloud Gemini / Local LLM 지원
- ⚡ **고성능 비동기 로깅 & UI 스트리밍**: Queue 기반 디스크 비동기 로거, 200ms DOM Throttling, Delta streaming(`since_index`), 영속적 레벨 필터링(`INFO`/`WARN`/`ERROR`/`DEBUG`)
- 📊 **다양한 리포트 포맷**: Markdown, HTML, Excel, PDF, PPTX 형식 리포트 내보내기 지원
- ⏰ **무인 정기점검 스케줄러**: UI 없이 백그라운드 크론(Cron) 루프로 정기 점검 자동 실행

---

## 🛠 사전 요구 사항 (Prerequisites)

- **OS**: Windows 10 / 11 (Microsoft Edge WebView2 Runtime 필수 포함)
- **Python**: Python 3.9 이상
- **네트워크**: 점검 대상 장비 또는 가상 실습 환경(EVE-NG 등)에 SSH 도달 가능한 네트워크 환경
- **의존성 패키지**: `requirements.txt`에 명시된 필수 라이브러리

---

## 🚀 빠른 시작 (Quick Start)

### 1. 패키지 설치 및 실행

```bash
# 필수 의존성 라이브러리 설치
pip install -r requirements.txt

# GUI 앱 실행 (기본 진입점)
python main.py
```

### 2. 무인 정기점검 스케줄러 실행 (UI 없이 실행)

```bash
# 지금 시각에 예정된 점검 작업 1회 실행
python -m engine.scheduler

# 백그라운드 크론 루프 실행 (매분 정기 체크)
python -m engine.scheduler --loop
```

> **참고**: 스케줄러 실행 일정은 `config/scheduler.yaml`에서 설정하며, UI 실행과 동일한 채점 파이프라인(`engine/grading.py`)을 공유합니다.

---

## 📖 사용자 작업 흐름 가이드 (User Workflow Guide)

사이드바 메뉴는 실제 업무 진행 순서에 따라 **준비 → 실행 → 결과 → 기타** 4개 그룹으로 구성되어 있습니다.

### 1️⃣ 준비 (Preparation)
* **1. 워크스페이스**: 고객사를 추가하고 정기점검 회차(프로파일)를 생성 또는 선택합니다.
  * *팁*: 동일 고객사의 두 번째 회차부터는 직전 회차의 장비 목록을 자동으로 물려받습니다.
* **2. 장비 목록**: 관리 IP, 포트, 계정을 입력하고 점검할 장비를 활성화합니다.
  * IP/계정 수정 시 자동으로 연결을 검증하며, 성공 시 장비가 알려준 `hostname`으로 이름을 자동 갱신합니다.
* **3. EVE 구성 불러오기 (선택)**: `.unl` topology 파일에서 장비 목록을 한 번에 가져옵니다.
* **4. 명령어 카탈로그**: 점검 시 장비에서 실행하여 수집할 CLI 커맨드를 활성화합니다.
* **5. 점검 항목**: 단계(Stage) 구성과 검사 항목 간 의존관계를 확인합니다. (읽기 전용)

### 2️⃣ 실행 (Execution)
* **6. 세션 터미널 (선택)**: 장비에 직접 SSH로 접속하여 수동 명령어를 확인합니다.
* **7. 수집/채점**: 점검 실행 버튼 클릭 시 `접속 → 수집 → 파싱 → 판정 → 채점 → 이력저장 → 리포트 생성` 과정이 일괄 처리됩니다.
* **8. 점검 로그**: 수집된 원본 CLI 로그 확인, 이상 탐지 및 외부 제출용 데이터 마스킹을 수행합니다.

### 3️⃣ 결과 (Results) & 4️⃣ 기타 (Settings & Logs)
* **9. 대시보드 / 발견사항 / 분석 / 보고서 / 이력**: 종합 건전성 점수, PASS/FAIL 판정 결과, AI 조치 권고안 및 내보내기 리포트를 확인합니다.
* **10. 환경 설정**: AI 제공자(API 키), SSH 공통 접속 인자 및 터미널 동작 설정을 맞춥니다. (모든 고객사/회차 공통 적용)
* **11. 전체 로그**: 실시간 실행 로그를 200ms 속도제어(Throttling) 및 레벨 필터(`INFO`/`WARN`/`ERROR`/`DEBUG`)로 스트리밍 확인하고 전체 세션 로그(.txt)를 내보냅니다.

---

## ⚙️ 주요 설정 파일 (Configuration Files)

애플리케이션 전역 설정 및 스케줄러 일정은 파일 단위로 관리됩니다:

- `ai_config.yaml`: AI 분석 제공자 (Cloud Gemini, Local LLM) 및 API 키/모델/`local_batching` 설정
- `connection.yaml`: SSH 접속 타임아웃, 재시도 횟수 및 공통 기본 계정 설정
- `config/scheduler.yaml`: 무인 정기점검 백그라운드 크론(Cron) 주기 및 대상 설정
- `ai_settings.yaml`: 프롬프트 서식 및 분석 출력 옵션 설정

---

## 🏗️ 아키텍처 및 파이프라인 개요 (Architecture & Pipeline Summary)

`engine/grading.py` 실행 시 `pipeline/`에 정의된 개별 파이프라인 단계(Step)들이 순차적으로 구동됩니다:

```
[1. CollectorStep]   장비 SSH 접속 및 raw CLI 출력 수집 (engine/collector.py)
        │
[2. ParserStep]      VendorDriver 기반 CLI 출력 구조화 파싱 (parsers/)
        │
[3. RuleEngineStep]  target_state 대조 및 PASS/FAIL/UNKNOWN 판정 (rule_engine/)
        │
[4. ScorerStep]      Stage 단위 점수 집계 (engine/scorer.py)
        │
[5. HistoryStep]     이력 저장 및 직전 회차 대비 Diff 분석 (engine/history.py)
        │
[6. AlarmStep]       Critical Finding 알림 통보 (alarm/)
        │
[7. AIAnalysisStep]  AI 요약 및 조치 권고안 생성 (core/ai/)
        │
[8. ReportStep]      Markdown/HTML/Excel/PPTX 리포트 내보내기 (report/)
```

> **상세 개발자 안내**: 파이프라인 확장 방법, 5대 워크스페이스 매니저 구조, API 계층 믹스인 설계 등은 [ARCHITECTURE.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/ARCHITECTURE.md) 문서를 참고하세요.

---

## 📂 프로젝트 디렉터리 구조 (Directory Structure)

```
main.py                 # 앱 유일 진입점 (pywebview 생성 및 Api 믹스인 합성)
web_ui/                 # Material Design 3 프런트엔드 (HTML/CSS/JS 및 에셋)
api/                    # GUI-Engine 간 비즈니스 API 믹스인 레이어
engine/                 # 장비 수집, 워크스페이스, 리포트, 스케줄러 핵심 로직
pipeline/               # 점검/채점 파이프라인 실행기 및 단계(Step) 정의
rule_engine/            # 판정 규칙 평가 엔진
parsers/                # 벤더별(Cisco, Arista) CLI 파싱 구현체
plugins/                # 벤더 드라이버 및 파서 레지스트리
core/                   # 공통 I/O, 경로 해석, AI 서비스, 로깅 infrastructure, 타임스탬프 유틸리티
ai_analysis/            # AI 로그 분석 라우터 및 vEOS-lab 프롬프트 템플릿
report/                 # Markdown, HTML, Excel, PPTX 렌더러 및 내보내기 플러그인
labs/                   # 랩 정의 (stages, target_state, catalog 등)
data/<customer>/<profile>/  # 정기점검 실행 워크스페이스 (raw, masked, parsed, reports)
```

---

## ⚠️ 알려진 제약 사항 (Known Constraints)

- **네트워크 접근성**: 실제 장비 수집 및 채점은 대상 네트워크 장비 또는 EVE-NG 등 가상 실습망에 SSH 접근이 가능한 환경에서 동작합니다.
- **벤더 드라이버 지원 범위**: Arista vEOS 및 physical 드라이버가 풀 기능 기준으로 지원되며, Cisco 드라이버는 부분 구현되어 있습니다.
- **후속 과제**: 구조적 리팩토링 항목 및 기술 부채 관련사항은 [ARCHITECTURE.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/ARCHITECTURE.md)의 *Known follow-ups* 섹션을 참고하세요.

---

## 📄 개발자 참고 문서 & 라이선스 (Developer Resources & License)

- **버전 변경 이력**: [CHANGELOG.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/CHANGELOG.md)
- **시스템 아키텍처 상세**: [ARCHITECTURE.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/ARCHITECTURE.md)
- **개발자 작업 가이드**: [DEVELOPER_GUIDE.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/DEVELOPER_GUIDE.md)
- **워크스페이스 데이터 배치**: [WORKSPACE_STRUCTURE.md](file:///c:/Users/USER/Documents/DWCTS/AI인사평가/AutoCheck/V0/lab_grader_V0.0.036/WORKSPACE_STRUCTURE.md)

**License**: Internal / Proprietary. All rights reserved.
