"""CloudAiApiMixin — 클라우드 AI 제공자별 API 키/모델 설정.

_load_ai_config()/AI_CONFIG_PATH/MODEL_PARAM_DEFAULTS는 LocalAiApiMixin이 정의하므로,
이 클래스는 항상 LocalAiApiMixin과 함께(SettingsApiMixin에서) 조합되어야 한다.

제공자 표(제공자 목록·모델 목록·연결 테스트 방법·배치 기본값)의 정본은 이 파일이 아니라
core/cloud_providers.py다 — 분석 쪽(ai_analysis/)도 같은 표를 읽어야 해서 공용 모듈로 뺐다.
제공자나 모델을 추가할 때는 그 파일만 고치면 되고, 이 파일과 UI는 손대지 않는다.
"""
from core import cloud_providers


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
            cloud_providers.max_tokens_for(provider_id, model_defaults.get("max_tokens")),
        )

    def _get_cloud_apis_provider(self, cfg):
        providers = cfg.setdefault("providers", [])
        node = next((p for p in providers if p.get("type") == "cloud_apis"), None)
        if node is None:
            node = {"type": "cloud_apis", "entries": []}
            providers.append(node)
        return node

    def remove_cloud_api(self, entry_id):
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        node["entries"] = [e for e in node["entries"] if e.get("id") != entry_id]
        self._save_ai_config(cfg)
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
            headers = {"x-api-key": key, "anthropic-version": cloud_providers.ANTHROPIC_VERSION}
        elif auth in ("google_api_key_header", "google_key_param"):
            # Gemini 는 쿼리스트링(?key=)도 받지만 URL 은 오류·로그에 그대로 찍히므로 헤더로 보낸다.
            headers["x-goog-api-key"] = key
        else:
            return {"ok": False, "detail": f"알 수 없는 인증 방식: {auth}"}

        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as resp:
                resp.read()
            return {"ok": True, "detail": "인증 성공"}
        except urllib.error.HTTPError as exc:
            return _test_failure(exc.code)
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
                # 봉인을 이 PC 에서 풀 수 없는 키(다른 PC 에서 옮겨 온 설정) — 화면이 '다시 입력'으로 안내한다.
                "key_unreadable": bool(e.get("_sealed_api_key")),
                "batch_chars": e.get("batch_chars", batch_chars),
                # 실제로 보내는 값 — 제공자 하한보다 작게 저장된 예전 값은 하한으로 보인다(분석도 그 값을 쓴다).
                "max_tokens": cloud_providers.max_tokens_for(provider, e.get("max_tokens", max_tokens)),
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
            problem = cloud_providers.endpoint_problem(ep)
            if problem:
                return {"error": problem}
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
        self._save_ai_config(cfg)
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
            problem = cloud_providers.endpoint_problem(ep) if ep else None
            if problem:
                return {"error": problem}
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
                entry["max_tokens"] = cloud_providers.max_tokens_for(
                    entry.get("provider", "anthropic"), max(100, int(fields["max_tokens"])))
            except (TypeError, ValueError):
                pass

        self._save_ai_config(cfg)
        return {"ok": True}


def _test_failure(code):
    """연결 테스트가 2xx 가 아닌 응답을 받았을 때의 결과. 성공은 2xx 뿐이다 — 예전에는 401/403 이 아니면 전부
    성공으로 봐서, 주소가 틀린 404 나 제공자 장애(5xx)도 '키는 거부되지 않았습니다'로 보였다.
    400/405 는 GET 을 받지 않는 주소(채팅 주소로 확인하는 직접입력·Perplexity)일 뿐일 수 있어 경고로 둔다 —
    서버에는 닿았지만 키는 확인하지 못한 것이다."""
    if code == 401:
        return {"ok": False, "detail": "인증 실패(401) — 키를 확인하세요."}
    if code == 402:
        return {"ok": False, "detail": "결제 문제(402) — 제공자 계정의 결제 상태를 확인하세요."}
    if code == 403:
        return {"ok": False, "detail": "권한 없음(403) — 이 키의 계정·작업공간 권한을 확인하세요."}
    if code == 404:
        return {"ok": False, "detail": "주소를 찾을 수 없음(404) — 엔드포인트 주소를 확인하세요."}
    if code == 429:
        return {"ok": False, "detail": "요청 한도 초과(429) — 잠시 후 다시 확인하세요."}
    if code >= 500:
        return {"ok": False, "detail": f"제공자 서버 오류(HTTP {code}) — 잠시 후 다시 확인하세요."}
    if code in (400, 405):
        return {"ok": False, "warn": True,
                "detail": f"서버에는 닿았지만(HTTP {code}) 이 주소는 GET 확인을 받지 않아 키는 확인하지 못했습니다 — "
                          "분석을 실행할 때 확인됩니다."}
    return {"ok": False, "detail": f"예상하지 못한 응답(HTTP {code}) — 주소와 키를 확인하세요."}
