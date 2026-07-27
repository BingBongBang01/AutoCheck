"""FindingsApiMixin — Findings(Jira 스타일) 데이터 제공만 담당."""


class FindingsApiMixin:
    def get_findings(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return []
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return []
        return latest.get("findings", [])   # v0.0.10 이전 세션엔 findings 키가 없을 수 있음(하위호환, 빈 리스트)


class HistoryApiMixin:
    def list_history(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return []
        from engine.history import list_sessions
        return list_sessions(project_id)

    def get_history(self, session):
        from engine.history import load_session
        return load_session(self._project(), session)

    def delete_history(self, session):
        from engine.history import delete_session
        return delete_session(self._project(), session)


class ArchitectureApiMixin:
    def get_architecture_status(self):
        return {
            "implemented": [
                {"name": "Finding 표준 스키마", "detail": "severity/status/owner/source, AI가 result 못 바꾸게 강제"},
                {"name": "ProjectContext / SessionContext", "detail": "상태만 보관, 로직 없음"},
                {"name": "Pipeline (PipelineStep + 실행기)", "detail": "Stage 추가 = Step 추가로 확장 (OCP)"},
                {"name": "VendorDriver (Arista)", "detail": "check_id -> 실제 CLI 변환, 30개 매핑"},
                {"name": "Parser Registry", "detail": "(vendor, check_id) 자동 탐색, 16개 파서 등록"},
                {"name": "Rule Engine 어댑터", "detail": "comparator.py 재사용 + Finding 변환"},
                {"name": "Command Catalog check_id 전환", "detail": "Driver 경유 결과가 기존 방식과 완전 일치 검증됨"},
                {"name": "Pipeline 단일 채점 경로", "detail": "engine/grading.py로 분리, 레거시 grade()/mock 폐기"},
                {"name": "Sanitizer", "detail": "Cloud 전송 직전 IP/MAC/호스트명 마스킹, 원본 불변"},
                {"name": "AIContextBuilder", "detail": "FAIL/UNKNOWN만 압축, Sanitizer와 역할 분리"},
                {"name": "Cloud AI 승인 게이트", "detail": "user_approved_cloud=True 없이는 절대 미호출"},
                {"name": "Report Plugin", "detail": "Markdown/Docx 등록, 신규 포맷 추가는 등록만 하면 됨"},
                {"name": "Device Inventory", "detail": "IP/계정 단일 소스, Import/자동할당/도달가능성"},
                {"name": "Finding severity/status 5단계", "detail": "Critical~Info / Open~Closed (Jira 스타일)"},
                {"name": "Health Score", "detail": "100점 시작, Rule 위반마다 감점, Device/Project 집계"},
                {"name": "main.py API 모듈화", "detail": "Api 클래스를 관심사별 mixin으로 분리(SRP)"},
                {"name": "web_ui/app.js 모듈화", "detail": "689줄 단일파일을 페이지별 14개 js 파일로 분리"},
                {"name": "Gemini API Provider", "detail": "PDF번역 프로그램의 Gemini+로컬NPU 폴백 패턴 반영"},
                {"name": "Analysis 탭", "detail": "Parser/Rule/Evidence/AI/Health/Vendor 통합 뷰"},
                {"name": "Inspection Profile 탭", "detail": "Stage 의존관계·커맨드·체크수 구조 뷰(읽기전용)"},
                {"name": "Knowledge 탭", "detail": "프로젝트별 Markdown 문서 탐색기(CRUD)"},
                {"name": "Device Inventory 연결 테스트", "detail": "장비별 개별 소켓 도달가능성 즉시 확인 버튼"},
            ],
            "pending": [
                {"name": "Cisco/Juniper/Fortigate/Linux VendorDriver", "detail": "Arista만 구현됨"},
                {"name": "Local AI(Ollama) 실제 연동", "detail": "라우터 구조는 있음, 실제 호출 미검증"},
                {"name": "StorageService 정식화", "detail": "projects/{id}/ 통합 폴더 구조로 재편 예정"},
                {"name": "Dashboard NOC 스타일 고도화", "detail": "Topology/Alarm 위젯 미구현"},
                {"name": "Maintenance / Scheduler", "detail": "Pipeline 주기 실행 오케스트레이션 미구현"},
                {"name": "Alarm (Event 기반)", "detail": "Finding 발생 시 알림 미구현"},
                {"name": "Inspection Profile 편집 기능", "detail": "현재 읽기 전용, 체크 추가/값 수정은 다음 버전"},
            ],
        }
