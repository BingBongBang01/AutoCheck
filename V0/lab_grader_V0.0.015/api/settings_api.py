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

    # ---------- ai_config.yaml (로컬 AI 모델 파라미터 / API 키) ----------
    AI_CONFIG_PATH = "ai_config.yaml"
    LOCAL_MODEL_CHOICES = [
        {"id": "gemma-3n-4b", "label": "Gemma 3n 4B (Lemonade NPU)"},
        {"id": "llama-3.2-3b", "label": "Llama 3.2 3B"},
        {"id": "phi-4-mini", "label": "Phi-4 Mini"},
    ]

    def _load_ai_config(self):
        import yaml
        if not os.path.exists(self.AI_CONFIG_PATH):
            return {"providers": []}
        with open(self.AI_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"providers": []}

    def get_local_ai_config(self):
        """설정 화면의 '로컬 AI' 섹션 — 모델 선택 + temperature/max_tokens 등 파라미터."""
        cfg = self._load_ai_config()
        local = next((p for p in cfg.get("providers", []) if p.get("type") == "local"), {})
        return {
            "endpoint": local.get("endpoint", "http://localhost:13305"),
            "model": local.get("model", self.LOCAL_MODEL_CHOICES[0]["id"]),
            "temperature": local.get("temperature", 0.3),
            "max_tokens": local.get("max_tokens", 800),
            "model_choices": self.LOCAL_MODEL_CHOICES,
        }

    def save_local_ai_config(self, settings):
        import yaml
        cfg = self._load_ai_config()
        providers = cfg.setdefault("providers", [])
        local = next((p for p in providers if p.get("type") == "local"), None)
        if local is None:
            local = {"type": "local"}
            providers.append(local)
        local["endpoint"] = settings.get("endpoint", "http://localhost:13305")
        local["model"] = settings.get("model", self.LOCAL_MODEL_CHOICES[0]["id"])
        try:
            local["temperature"] = max(0.0, min(2.0, float(settings.get("temperature", 0.3))))
        except (TypeError, ValueError):
            local["temperature"] = 0.3
        try:
            local["max_tokens"] = max(1, int(settings.get("max_tokens", 800)))
        except (TypeError, ValueError):
            local["max_tokens"] = 800
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return True

    def test_local_ai_connection(self, endpoint):
        """로컬 NPU(Lemonade 등) 서버에 짧은 타임아웃으로 핑을 보내 도달 가능한지 확인."""
        import urllib.request
        import urllib.error
        endpoint = (endpoint or "").rstrip("/")
        if not endpoint:
            return {"ok": False, "detail": "엔드포인트를 입력하세요."}
        try:
            urllib.request.urlopen(endpoint, timeout=3)
            return {"ok": True, "detail": f"{endpoint} 응답함"}
        except urllib.error.HTTPError:
            # 서버가 살아있고 HTTP로 응답은 했으므로(404 등이라도) 연결 자체는 성공으로 간주.
            return {"ok": True, "detail": f"{endpoint} 응답함"}
        except Exception as exc:
            return {"ok": False, "detail": f"연결 실패: {exc}"}

    # ---------- API 키 (Anthropic / Gemini) ----------
    API_KEY_PROVIDERS = {
        "api": {"label": "Anthropic API", "env": "ANTHROPIC_API_KEY"},
        "gemini": {"label": "Gemini API", "env": "GEMINI_API_KEY"},
    }

    def get_api_key_settings(self):
        cfg = self._load_ai_config()
        result = {}
        for provider_type, meta in self.API_KEY_PROVIDERS.items():
            provider = next((p for p in cfg.get("providers", []) if p.get("type") == provider_type), {})
            env_name = provider.get("api_key_env", meta["env"])
            result[provider_type] = {
                "label": meta["label"],
                "api_key_env": env_name,
                "has_env_value": bool(os.environ.get(env_name)),
                "has_saved_key": bool(provider.get("api_key")),
            }
        return result

    def save_api_key(self, provider_type, api_key):
        """API 키를 ai_config.yaml에 로컬 저장하고, 현재 실행 중인 프로세스 환경변수에도 즉시 반영."""
        import yaml
        if provider_type not in self.API_KEY_PROVIDERS:
            return {"error": f"알 수 없는 provider: {provider_type}"}
        if not api_key or not api_key.strip():
            return {"error": "API 키를 입력하세요."}
        cfg = self._load_ai_config()
        providers = cfg.setdefault("providers", [])
        provider = next((p for p in providers if p.get("type") == provider_type), None)
        if provider is None:
            provider = {"type": provider_type}
            providers.append(provider)
        env_name = provider.get("api_key_env", self.API_KEY_PROVIDERS[provider_type]["env"])
        provider["api_key_env"] = env_name
        provider["api_key"] = api_key.strip()
        os.environ[env_name] = api_key.strip()
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return {"ok": True}

    def test_api_key(self, provider_type, api_key):
        """입력된 키로 간단한 API 호출을 시도해 유효성을 확인(요금 발생 없는 최소 호출)."""
        import urllib.request
        import urllib.error
        if provider_type not in self.API_KEY_PROVIDERS:
            return {"ok": False, "detail": "알 수 없는 provider"}
        api_key = (api_key or "").strip() or os.environ.get(self.API_KEY_PROVIDERS[provider_type]["env"], "")
        if not api_key:
            return {"ok": False, "detail": "API 키를 입력하세요."}
        try:
            if provider_type == "api":
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
            else:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1/models?key={api_key}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            return {"ok": True, "detail": "인증 성공"}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {"ok": False, "detail": f"인증 실패({exc.code}) — 키를 확인하세요."}
            return {"ok": True, "detail": f"서버 응답함(HTTP {exc.code})"}
        except Exception as exc:
            return {"ok": False, "detail": f"연결 실패: {exc}"}
