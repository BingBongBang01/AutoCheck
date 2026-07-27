"""CloudAiApiMixin — 클라우드 AI 제공자(Anthropic/Gemini/OpenAI) API 키/모델 설정.

_load_ai_config()/AI_CONFIG_PATH/MODEL_PARAM_DEFAULTS는 LocalAiApiMixin이 정의하므로,
이 클래스는 항상 LocalAiApiMixin과 함께(SettingsApiMixin에서) 조합되어야 한다.
"""
from core.atomic_io import dump_yaml_atomic


class CloudAiApiMixin:
    CLOUD_PROVIDER_TYPES = [
        {
            "id": "anthropic",
            "label": "Anthropic (Claude)",
            "models": ["claude-3-5-sonnet-20240620", "claude-3-haiku-20240307"]
        },
        {
            "id": "gemini",
            "label": "Google Gemini",
            "models": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
        },
        {
            "id": "openai",
            "label": "OpenAI",
            "models": ["gpt-4o", "gpt-4o-mini"]
        },
    ]

    def _get_cloud_apis_provider(self, cfg):
        providers = cfg.setdefault("providers", [])
        node = next((p for p in providers if p.get("type") == "cloud_apis"), None)
        if node is None:
            node = {"type": "cloud_apis", "entries": []}
            providers.append(node)
        return node

    def remove_cloud_api(self, entry_id):
        import yaml
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        node["entries"] = [e for e in node["entries"] if e.get("id") != entry_id]
        dump_yaml_atomic(cfg, self.AI_CONFIG_PATH)
        return {"ok": True}

    def test_cloud_api(self, entry_id, api_key=None):
        """저장된 키(또는 아직 저장 전이면 입력창의 임시 키)로 최소 호출을 시도해 유효성 확인."""
        import urllib.request
        import urllib.error
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry = next((e for e in node["entries"] if e.get("id") == entry_id), None)
        if entry is None:
            return {"ok": False, "detail": "존재하지 않는 항목입니다."}
        key = (api_key or "").strip() or entry.get("api_key", "")
        if not key:
            return {"ok": False, "detail": "API 키를 입력하세요."}
        provider = entry.get("provider", "anthropic")
        try:
            if provider == "anthropic":
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
            elif provider == "openai":
                req = urllib.request.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            else:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1/models?key={key}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            return {"ok": True, "detail": "인증 성공"}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {"ok": False, "detail": f"인증 실패({exc.code}) — 키를 확인하세요."}
            return {"ok": True, "detail": f"서버 응답함(HTTP {exc.code})"}
        except Exception as exc:
            return {"ok": False, "detail": f"연결 실패: {exc}"}

    def get_cloud_apis(self):
        """저장된 클라우드 API 설정 목록을 반환하며, 모델별 오버플로우 방지 옵션 포함."""
        cfg = self._load_ai_config()
        node = next((p for p in cfg.get("providers", []) if p.get("type") == "cloud_apis"), {"entries": []})
        return {
            "provider_types": self.CLOUD_PROVIDER_TYPES,
            "entries": [
                {
                    "id": e.get("id"),
                    "name": e.get("name", ""),
                    "provider": e.get("provider", "anthropic"),
                    "model": e.get("model", ""),
                    "enabled": bool(e.get("enabled")),
                    "has_key": bool(e.get("api_key")),
                    # 클라우드 전용 오버플로우 수치 반환 (없을 경우 Provider별 기본값 할당)
                    "batch_chars": e.get("batch_chars", 30000 if e.get("provider") == "openai" else (40000 if e.get("provider") == "gemini" else 20000)),
                    "max_tokens": e.get("max_tokens", 1500),
                }
                for e in node.get("entries", [])
            ],
        }

    def add_cloud_api(self, name, provider, api_key=None):
        import yaml
        import uuid
        if provider not in [p["id"] for p in self.CLOUD_PROVIDER_TYPES]:
            return {"error": f"지원하지 않는 provider: {provider}"}

        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry_id = uuid.uuid4().hex[:12]
        key = (api_key or "").strip()

        default_model = ""
        for p in self.CLOUD_PROVIDER_TYPES:
            if p["id"] == provider and "models" in p:
                default_model = p["models"][0]
                break

        # 모델별 기본값이 있으면 그걸 쓰고, 없으면 provider 단위 기본값으로 폴백
        model_defaults = self.MODEL_PARAM_DEFAULTS.get(default_model, {})
        default_batch_chars = model_defaults.get(
            "batch_chars", 30000 if provider == "openai" else (40000 if provider == "gemini" else 20000))
        default_max_tokens = model_defaults.get("max_tokens", 1500)

        node["entries"].append({
            "id": entry_id,
            "name": (name or "").strip() or f"{provider}-{entry_id[:4]}",
            "provider": provider,
            "model": default_model,
            "api_key": key,
            "enabled": bool(key),
            "batch_chars": default_batch_chars,
            "max_tokens": default_max_tokens,
        })
        dump_yaml_atomic(cfg, self.AI_CONFIG_PATH)
        return {"ok": True, "id": entry_id}

    def update_cloud_api(self, entry_id, fields):
        """클라우드 항목(name / provider / enabled / api_key / model)과 함께 오버플로우 옵션(batch_chars / max_tokens) 수정 반영."""
        import yaml
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry = next((e for e in node["entries"] if e.get("id") == entry_id), None)
        if entry is None:
            return {"error": "해당 항목을 찾을 수 없습니다."}

        if "name" in fields and fields["name"] is not None:
            entry["name"] = fields["name"].strip() or entry["name"]
        if "provider" in fields and fields["provider"] in [p["id"] for p in self.CLOUD_PROVIDER_TYPES]:
            entry["provider"] = fields["provider"]
        model_changed = False
        if "model" in fields:
            new_model = fields["model"].strip()
            model_changed = new_model != entry.get("model")
            entry["model"] = new_model
        if "enabled" in fields:
            entry["enabled"] = bool(fields["enabled"])
        if fields.get("api_key"):
            entry["api_key"] = fields["api_key"].strip()

        # 모델이 바뀌었고 batch_chars/max_tokens를 이번 호출에서 명시적으로 같이 넘기지 않았다면
        # 그 모델에 맞는 기본값을 자동 적용
        if model_changed and "batch_chars" not in fields and "max_tokens" not in fields:
            model_defaults = self.MODEL_PARAM_DEFAULTS.get(entry["model"])
            if model_defaults:
                if "batch_chars" in model_defaults:
                    entry["batch_chars"] = model_defaults["batch_chars"]
                if "max_tokens" in model_defaults:
                    entry["max_tokens"] = model_defaults["max_tokens"]

        # 클라우드 전용 오버플로우 수치 갱신 처리
        if "batch_chars" in fields:
            try:
                entry["batch_chars"] = max(1000, int(fields["batch_chars"]))
            except (TypeError, ValueError):
                pass
        if "max_tokens" in fields:
            try:
                entry["max_tokens"] = max(100, int(fields["max_tokens"]))
            except (TypeError, ValueError):
                pass

        dump_yaml_atomic(cfg, self.AI_CONFIG_PATH)
        return {"ok": True}
