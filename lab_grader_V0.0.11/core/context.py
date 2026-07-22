"""
ProjectContext / SessionContext — 상태(데이터)만 보관하고 행동(로직)은 없음.
"Context 객체는 상태만, 로직은 서비스 객체(Pipeline/RuleEngine/StorageService)로"
원칙을 강제하기 위해 의도적으로 메서드를 거의 안 둔다.
"""
from dataclasses import dataclass, field
from typing import Optional
import datetime


@dataclass
class ProjectContext:
    project_id: str
    kind: str = "lab"          # lab | customer | maintenance (Project 통합 원칙 — 모델 분리 안 함)
    paths: dict = field(default_factory=dict)   # project_paths() 결과를 그대로 보관

    # 편의상 참조 헬퍼 — 계산/실행 로직 아님, 단순 dict 조회 래퍼라 로직 없음 원칙에 어긋나지 않음
    def path(self, key):
        return self.paths.get(key)


@dataclass
class SessionContext:
    project: ProjectContext
    session_id: str
    started_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    findings: list = field(default_factory=list)   # Finding 객체 리스트, Pipeline Step들이 채워나감
    raw_by_device: dict = field(default_factory=dict)   # collector 결과
    scored: list = field(default_factory=list)          # 기존 scorer.score_all() 결과(과도기 호환용)
    elapsed_sec: float = 0.0
    data: dict = field(default_factory=dict)   # Step 간 임시 전달값 (collected_vlan 등, 아직 정식 스키마 없는 것)

    def add_finding(self, finding):
        self.findings.append(finding)

    def findings_by_result(self, result):
        return [f for f in self.findings if f.result == result]
