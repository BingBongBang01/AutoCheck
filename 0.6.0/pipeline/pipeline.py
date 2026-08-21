"""
Pipeline — PipelineStep 리스트를 순서대로 실행하는 실행기.
GUI/스케줄러는 engine.grading을 통해 이 Pipeline만 호출한다(개별 Step을 직접 호출하지 않음 — 문서의
"GUI에서 Collector 직접 호출 금지" 원칙).

Event Bus 연동: Collector -> Parser -> Rule -> Export(Report) -> GUI가 서로 직접
호출하지 않고 이벤트로만 통신하게 하기 위해, bus가 주어지면 각 스텝 실행 전후
"pipeline.<step_name>.started"/"completed"(실패 시 "failed")를 publish한다.
GUI는 core/event_bus.py를 구독하기만 하면 되고, 이 Pipeline이 무슨 스텝으로
구성됐는지 몰라도 된다 — 스텝을 추가/제거해도 GUI 쪽 구독 코드는 안 바뀜.
"""
from core.event_bus import bus as default_bus


class Pipeline:
    def __init__(self, steps, on_step=None, bus=None):
        self.steps = steps
        self.on_step = on_step   # 선택: 각 스텝 실행 전후 콜백(로깅/진행률 표시용) — Event Bus 이전부터 있던 경로, 그대로 유지
        self.bus = bus if bus is not None else default_bus

    def run(self, ctx):
        for step in self.steps:
            if self.on_step:
                self.on_step(step.name, "시작")
            if self.bus:
                self.bus.publish(f"pipeline.{step.name}.started", {"session_id": getattr(ctx, "session_id", None)})
            try:
                ctx = step.run(ctx)
            except Exception as e:
                if self.bus:
                    self.bus.publish(f"pipeline.{step.name}.failed",
                                     {"session_id": getattr(ctx, "session_id", None), "error": str(e)})
                raise
            if self.on_step:
                self.on_step(step.name, "완료")
            if self.bus:
                self.bus.publish(f"pipeline.{step.name}.completed", {"session_id": getattr(ctx, "session_id", None)})
        return ctx

    def step_names(self):
        return [s.name for s in self.steps]
