"""CloudAiApiMixin — 클라우드 AI 제공자별 API 키/모델 설정.

_load_ai_config()/AI_CONFIG_PATH/MODEL_PARAM_DEFAULTS는 LocalAiApiMixin이 정의하므로,
이 클래스는 항상 LocalAiApiMixin과 함께(SettingsApiMixin에서) 조합되어야 한다.

제공자 표(제공자 목록·모델 목록·연결 테스트 방법·배치 기본값)의 정본은 이 파일이 아니라
core/cloud_providers.py다 — 분석 쪽(ai_analysis/)도 같은 표를 읽어야 해서 공용 모듈로 뺐다.
제공자나 모델을 추가할 때는 그 파일만 고치면 되고, 이 파일과 UI는 손대지 않는다.
"""
from core import cloud_providers
from core.atomic_io import dump_yaml_atomic


class CloudAiApiMixin:
    CLOUD_PROVIDER_TYPES = cloud_providers.CLOUD_PROVIDER_TYPES

    def _provider_type(self, provider_id):
        """provider id -> CLOUD_PROVIDER_TYPES 항목(없으면 None)."""
        return next((p for p in self.CLOUD_PROVIDER_TYPES if p["id"] == provider_id), None)

    def _provider_default_model(self, provider_id):
        p = self._provider_type(provider_id)
        return (p.get("models") or [""])[0] if p else ""

    def _batch_defaults(self, provider_id, model):
        """(batch_chars, max_tokens) — 모델별 값이 있으면 그걸, 없으면 제공자 기본값.
        제공자 표에 값을 적어 두므로 새 제공자를 추가해도 여기 손댈 필요가 없다."""
        p = self._provider_type(provider_id) or {}
        model_defaults = self.MODEL_PARAM_DEFAULTS.get(model, {})
        return (
            model_defaults.get("batch_chars", p.get("batch_chars", 20000)),
            model_defaults.get("max_tokens", p.get("max_tokens", 1500)),
        )

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
        """저장된 키(또는 아직 저장 전이면 입력창의 임시 키)로 최소 호출을 시도해 유효성 확인.

        제공자별 분기를 if문으로 나열하지 않고 CLOUD_PROVIDER_TYPES의 test 설정을 읽는다 —
        제공자를 추가할 때 이 함수는 고치지 않는다."""
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
        spec = (self._provider_type(provider) or {}).get("test")
        if not spec:
            return {"ok": False, "detail": f"연결 테스트를 지원하지 않는 제공자입니다: {provider}"}

        # 직접입력 제공자는 표에 테스트 주소가 없다 — 사용자가 넣은 엔드포인트로 확인한다.
        url = spec.get("url") or entry.get("endpoint", "")
        if not url:
            return {"ok": False, "detail": "API 주소(엔드포인트)를 먼저 입력하고 저장하세요."}
        headers = {}
        auth = spec.get("auth", "bearer")
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        elif auth == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        elif auth == "google_key_param":
            # Gemini는 헤더가 아니라 쿼리스트링으로 키를 받는다.
            url = f"{url}?key={key}"
        else:
            return {"ok": False, "detail": f"알 수 없는 인증 방식: {auth}"}

        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as resp:
                resp.read()
            return {"ok": True, "detail": "인증 성공"}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {"ok": False, "detail": f"인증 실패({exc.code}) — 키를 확인하세요."}
            # 405/400 등은 엔드포인트가 GET을 안 받는 것뿐 — 키 자체는 통과한 것으로 본다.
            return {"ok": True, "detail": f"서버 응답함(HTTP {exc.code}) — 키는 거부되지 않았습니다."}
        except Exception as exc:
            return {"ok": False, "detail": f"연결 실패: {exc}"}

    def get_cloud_apis(self):
        """저장된 클라우드 API 설정 목록을 반환하며, 모델별 오버플로우 방지 옵션 포함."""
        cfg = self._load_ai_config()
        node = next((p for p in cfg.get("providers", []) if p.get("type") == "cloud_apis"), {"entries": []})
        entries = []
        for e in node.get("entries", []):
            provider = e.get("provider", "anthropic")
            model = e.get("model", "")
            batch_chars, max_tokens = self._batch_defaults(provider, model)
            entries.append({
                "id": e.get("id"),
                "name": e.get("name", ""),
                "provider": provider,
                "model": model,
                "enabled": bool(e.get("enabled")),
                "has_key": bool(e.get("api_key")),
                "batch_chars": e.get("batch_chars", batch_chars),
                "max_tokens": e.get("max_tokens", max_tokens),
                # 직접입력 제공자의 주소. 목록에 있는 제공자는 빈 문자열.
                "endpoint": e.get("endpoint", ""),
                # 저장된 모델이 드롭다운 목록에 없으면 화면에서 '직접입력' 칸으로 보여야 한다.
                "model_is_custom": bool(model) and model not in cloud_providers.models_of(provider),
            })
        return {
            "provider_types": self.CLOUD_PROVIDER_TYPES,
            "custom_model_sentinel": cloud_providers.CUSTOM_MODEL_SENTINEL,
            "entries": entries,
        }

    def add_cloud_api(self, name, provider, api_key=None, model=None, endpoint=None):
        """새 클라우드 API 항목 추가.

        model: 드롭다운에서 고른 모델, 또는 '직접입력'으로 넣은 임의의 모델 ID.
               제공자들이 모델을 수시로 추가하므로 목록에 없는 값도 그대로 받는다.
        endpoint: provider='custom'(직접입력)일 때 호출할 chat completions 주소.
                  목록에 있는 제공자는 core/cloud_providers.py의 주소를 쓰므로 비워 둔다."""
        import uuid
        spec = self._provider_type(provider)
        if spec is None:
            return {"error": f"지원하지 않는 provider: {provider}"}

        chosen = (model or "").strip()
        if not chosen:
            chosen = self._provider_default_model(provider)
        ep = (endpoint or "").strip()
        if cloud_providers.needs_endpoint(provider):
            # 직접입력 제공자는 주소와 모델을 알 수 없으니 둘 다 필수다.
            if not ep:
                return {"error": "직접입력 제공자는 API 주소(엔드포인트)를 입력해야 합니다."}
            if not ep.lower().startswith(("http://", "https://")):
                return {"error": "엔드포인트는 http:// 또는 https:// 로 시작해야 합니다."}
            if not chosen:
                return {"error": "직접입력 제공자는 모델 ID를 입력해야 합니다."}

        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry_id = uuid.uuid4().hex[:12]
        key = (api_key or "").strip()

        batch_chars, max_tokens = self._batch_defaults(provider, chosen)
        entry = {
            "id": entry_id,
            "name": (name or "").strip() or f"{provider}-{entry_id[:4]}",
            "provider": provider,
            "model": chosen,
            "api_key": key,
            "enabled": bool(key),
            "batch_chars": batch_chars,
            "max_tokens": max_tokens,
        }
        if ep:
            # 분석 쪽 핸들러는 api_cfg["endpoint"]를 제공자 표보다 먼저 본다.
            entry["endpoint"] = ep
        node["entries"].append(entry)
        dump_yaml_atomic(cfg, self.AI_CONFIG_PATH)
        return {"ok": True, "id": entry_id}

    def update_cloud_api(self, entry_id, fields):
        """클라우드 항목(name / provider / enabled / api_key / model)과 함께 오버플로우 옵션(batch_chars / max_tokens) 수정 반영."""
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry = next((e for e in node["entries"] if e.get("id") == entry_id), None)
        if entry is None:
            return {"error": "해당 항목을 찾을 수 없습니다."}

        if "name" in fields and fields["name"] is not None:
            entry["name"] = fields["name"].strip() or entry["name"]
        model_changed = False
        if "provider" in fields and self._provider_type(fields["provider"]) is not None:
            if fields["provider"] != entry.get("provider"):
                # 제공자가 바뀌면 이전 제공자의 모델 ID/주소는 무효다 — 새 제공자 기준으로 맞춘다.
                entry["provider"] = fields["provider"]
                entry["model"] = self._provider_default_model(fields["provider"])
                entry.pop("endpoint", None)
                model_changed = True
        if "model" in fields:
            # 목록에 없는 모델도 그대로 받는다('직접입력') — 제공자들이 모델을 수시로 추가하는데
            # 표에 없다는 이유로 막으면 새 모델을 쓸 수 없다.
            new_model = (fields["model"] or "").strip()
            model_changed = model_changed or new_model != entry.get("model")
            entry["model"] = new_model
        if "endpoint" in fields:
            ep = (fields["endpoint"] or "").strip()
            if ep and not ep.lower().startswith(("http://", "https://")):
                return {"error": "엔드포인트는 http:// 또는 https:// 로 시작해야 합니다."}
            if ep:
                entry["endpoint"] = ep
            else:
                entry.pop("endpoint", None)

        if cloud_providers.needs_endpoint(entry.get("provider")):
            if not entry.get("endpoint"):
                return {"error": "직접입력 제공자는 API 주소(엔드포인트)를 입력해야 합니다."}
            if not entry.get("model"):
                return {"error": "직접입력 제공자는 모델 ID를 입력해야 합니다."}
        if "enabled" in fields:
            entry["enabled"] = bool(fields["enabled"])
        if fields.get("api_key"):
            entry["api_key"] = fields["api_key"].strip()

        # 모델이 바뀌었고 batch_chars/max_tokens를 이번 호출에서 명시적으로 같이 넘기지 않았다면
        # 그 모델(없으면 제공자)에 맞는 기본값을 자동 적용
        if model_changed and "batch_chars" not in fields and "max_tokens" not in fields:
            entry["batch_chars"], entry["max_tokens"] = self._batch_defaults(
                entry.get("provider", "anthropic"), entry["model"])

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
