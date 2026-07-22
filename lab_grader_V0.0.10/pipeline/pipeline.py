"""
Pipeline — PipelineStep 리스트를 순서대로 실행하는 실행기.
GUI/main.py는 이 Pipeline만 호출한다(개별 Step을 직접 호출하지 않음 — 문서의
"GUI에서 Collector 직접 호출 금지" 원칙).
"""


class Pipeline:
    def __init__(self, steps, on_step=None):
        self.steps = steps
        self.on_step = on_step   # 선택: 각 스텝 실행 전후 콜백(로깅/진행률 표시용)

    def run(self, ctx):
        for step in self.steps:
            if self.on_step:
                self.on_step(step.name, "시작")
            ctx = step.run(ctx)
            if self.on_step:
                self.on_step(step.name, "완료")
        return ctx

    def step_names(self):
        return [s.name for s in self.steps]
