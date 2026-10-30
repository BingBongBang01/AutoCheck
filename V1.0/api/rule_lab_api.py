"""RuleLabApiMixin — 실시간 감시의 '규칙 실험실'(IMPROVEMENT_PLAN 4-8).

판정은 engine/rule_lab.py 가 실시간 감시와 같은 판정기로 한다. 장비를 고르면 실시간 감시가 들고 있는 Baseline 으로
diff 판정까지 같게 한다 — 그래서 RealtimeWatchApiMixin(_realtime)과 함께 Api 에 조합돼야 한다.
"""

_MAX_TEXT = 200_000      # 붙여넣기 한 번의 상한(글자) — 실험실은 몇 줄을 들여다보는 곳이다


class RuleLabApiMixin:
    def run_rule_lab(self, text, device=""):
        """붙여넣은 줄을 diff 엔진과 규칙 엔진에 한 줄씩 넣어 층마다의 결과를 돌려준다.
        반환: {ok, device, baseline, lines, truncated, summary} 또는 {error}."""
        from engine import rule_lab
        text = str(text or "")
        if not text.strip():
            return {"error": "판정할 줄을 붙여넣으세요."}
        if len(text) > _MAX_TEXT:
            return {"error": f"너무 깁니다({len(text):,}자) — {_MAX_TEXT:,}자 안으로 줄여 주세요."}
        device = str(device or "").strip()
        store = self._realtime().baseline_store() if device else None
        return {"ok": True, **rule_lab.run_lab(text, device=device, store=store)}

    def lint_log_rules(self):
        """규칙 파일(config/log_rules.json) 점검 — 판정을 조용히 바꾸는 실수 목록."""
        from engine import rule_lab
        return {"ok": True, **rule_lab.lint_rules()}
