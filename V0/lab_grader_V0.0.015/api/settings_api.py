"""SettingsApiMixin — AI 제공자 우선순위(드래그 재정렬) 저장/조회."""
import os

DEFAULT_AI_ORDER = [
    {"id": "api", "label": "API (Anthropic)", "desc": "환경변수 ANTHROPIC_API_KEY 필요", "icon": "cloud"},
    {"id": "gemini", "label": "API (Gemini)", "desc": "환경변수 GEMINI_API_KEY 필요", "icon": "cloud"},
    {"id": "local", "label": "로컬 NPU (Gemma/Lemonade)", "desc": "http://localhost:13305", "icon": "memory"},
    {"id": "rule_based", "label": "규칙기반", "desc": "항상 사용 가능(최종 안전망)", "icon": "rule"},
]


class SettingsApiMixin:
    def get_ai_settings(self):
        import yaml
        if not os.path.exists("ai_settings.yaml"):
            return {"order": [p["id"] for p in DEFAULT_AI_ORDER], "providers": DEFAULT_AI_ORDER}
        with open("ai_settings.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        order = cfg.get("order") or [p["id"] for p in DEFAULT_AI_ORDER]
        by_id = {p["id"]: p for p in DEFAULT_AI_ORDER}
        providers = [by_id[i] for i in order if i in by_id]
        for p in DEFAULT_AI_ORDER:
            if p["id"] not in order:
                providers.append(p)
        return {"order": [p["id"] for p in providers], "providers": providers}

    def save_ai_settings(self, order):
        import yaml
        with open("ai_settings.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"order": list(order)}, f, allow_unicode=True, sort_keys=False)
        return True
