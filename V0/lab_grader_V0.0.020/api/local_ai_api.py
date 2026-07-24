"""LocalAiApiMixin — 로컬 AI 모델(Lemonade NPU 등) 파라미터/헬스체크/로드-언로드, 배치 오버플로우 설정.

ai_config.yaml의 스키마와 MODEL_PARAM_DEFAULTS는 CloudAiApiMixin과 공유되므로,
이 클래스가 항상 CloudAiApiMixin과 함께(SettingsApiMixin에서) 조합되어야 한다.
"""


class LocalAiApiMixin:
    AI_CONFIG_PATH = "ai_config.yaml"
    LOCAL_MODEL_CHOICES = [
        {"id": "gemma-3n-4b", "label": "Gemma 3n 4B (Lemonade NPU)"},
        {"id": "llama-3.2-3b", "label": "Llama 3.2 3B"},
        {"id": "phi-4-mini", "label": "Phi-4 Mini"},
    ]

    # 모델별 배치 문자 수/세그먼트 수/max_tokens 기본값 — 모델의 컨텍스트 윈도우·응답 특성에 맞춰
    # 모델 선택 시 자동으로 적용된다(로컬은 batch_chars/batch_segs/max_tokens 전부,
    # 클라우드는 provider별 컨텍스트 윈도우가 이미 크므로 batch_chars/max_tokens만 사용).
    MODEL_PARAM_DEFAULTS = {
        # 로컬 NPU 모델
        "gemma-3n-4b": {"batch_chars": 3000, "batch_segs": 10, "max_tokens": 1000},
        "llama-3.2-3b": {"batch_chars": 2500, "batch_segs": 8, "max_tokens": 800},
        "phi-4-mini": {"batch_chars": 3500, "batch_segs": 12, "max_tokens": 1200},
        # 클라우드 모델
        "claude-3-5-sonnet-20240620": {"batch_chars": 20000, "max_tokens": 1500},
        "claude-3-haiku-20240307": {"batch_chars": 15000, "max_tokens": 1000},
        "gemini-3.5-flash-lite": {"batch_chars": 35000, "max_tokens": 1200},
        "gemini-3.1-flash-lite": {"batch_chars": 35000, "max_tokens": 1200},
        "gemini-3.6-flash": {"batch_chars": 45000, "max_tokens": 1800},
        "gpt-4o": {"batch_chars": 30000, "max_tokens": 1500},
        "gpt-4o-mini": {"batch_chars": 25000, "max_tokens": 1000},
    }

    def get_model_param_defaults(self, model_id):
        """설정 화면에서 모델 변경 시 배치/세그먼트/max_tokens 추천값을 즉시 보여주기 위한 조회용."""
        return self.MODEL_PARAM_DEFAULTS.get(model_id)

    def _load_ai_config(self):
        import os
        import yaml
        if not os.path.exists(self.AI_CONFIG_PATH):
            return {"providers": []}
        with open(self.AI_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"providers": []}

    def get_local_ai_config(self):
        """설정 화면의 '로컬 AI' 섹션 — 모델 선택 + temperature 등 파라미터.
        model_choices는 정적 기본값이며, lemonade-server가 떠 있으면 list_lemonade_models()로
        실제 설치된 모델 목록을 새로고침해서 대체할 수 있다."""
        cfg = self._load_ai_config()
        local = next((p for p in cfg.get("providers", []) if p.get("type") == "local"), {})
        return {
            "endpoint": local.get("endpoint", "http://localhost:13305"),
            "model": local.get("model", self.LOCAL_MODEL_CHOICES[0]["id"]),
            "temperature": local.get("temperature", 0.3),
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
        prev_model = local.get("model")
        new_model = settings.get("model", self.LOCAL_MODEL_CHOICES[0]["id"])
        local["endpoint"] = settings.get("endpoint", "http://localhost:13305")
        local["model"] = new_model
        try:
            local["temperature"] = max(0.0, min(2.0, float(settings.get("temperature", 0.3))))
        except (TypeError, ValueError):
            local["temperature"] = 0.3

        # 모델이 바뀌면 그 모델에 맞는 배치/세그먼트/max_tokens 기본값을 자동 적용
        if new_model != prev_model:
            defaults = self.MODEL_PARAM_DEFAULTS.get(new_model)
            if defaults:
                cfg["local_batching"] = {
                    "batch_chars": defaults.get("batch_chars", 3000),
                    "batch_segs": defaults.get("batch_segs", 10),
                    "max_tokens": defaults.get("max_tokens", 1000),
                }

        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return True

    def list_lemonade_models(self, endpoint):
        """lemonade-server(또는 ollama/lmstudio 등 OpenAI 호환 서버)에 설치된 모델 목록을 조회.
        PDF번역 프로그램의 fetch_local_models()와 동일하게 /api/v1/models(lemonade 기본 경로)를
        먼저 시도하고 실패하면 /v1/models로 재시도한다."""
        import urllib.request
        import json as _json
        endpoint = (endpoint or "http://localhost:13305").rstrip("/")
        for path in ("/api/v1/models", "/v1/models"):
            try:
                with urllib.request.urlopen(f"{endpoint}{path}", timeout=5) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                items = data.get("data", data if isinstance(data, list) else [])
                ids = [it.get("id") if isinstance(it, dict) else str(it) for it in items if it]
                if ids:
                    return {"ok": True, "models": [{"id": m, "label": m} for m in ids]}
            except Exception:
                continue
        return {"ok": False, "models": [], "detail": "모델 목록을 가져오지 못했습니다. 서버가 실행 중인지 확인하세요."}

    # ---------- 컨텍스트 오버플로우 방지(배치 문자 수 / 세그먼트 수 / max_tokens) ----------
    def get_batching_settings(self):
        cfg = self._load_ai_config()
        # 기존 batching 키를 local_batching으로 마이그레이션하거나 그대로 로컬 전용으로 사용
        b = cfg.get("local_batching") or cfg.get("batching", {})
        return {
            "batch_chars": b.get("batch_chars", 3000),
            "batch_segs": b.get("batch_segs", 10),
            "max_tokens": b.get("max_tokens", 1000),
        }

    def save_batching_settings(self, settings):
        import yaml
        cfg = self._load_ai_config()
        def _int(key, default, minimum=1):
            try:
                return max(minimum, int(settings.get(key, default)))
            except (TypeError, ValueError):
                return default

        cfg["local_batching"] = {
            "batch_chars": _int("batch_chars", 3000),
            "batch_segs": _int("batch_segs", 10),
            "max_tokens": _int("max_tokens", 1000),
        }
        # 레거시 공통 batching 설정 데이터 정리
        if "batching" in cfg:
            del cfg["batching"]

        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return True

    def test_local_ai_connection(self, endpoint):
        """로컬 NPU(Lemonade 등) 서버 연결 테스트. 베이스 URL에 대한 단순 핑은 서버가 살아있기만
        하면 404여도 성공으로 오판할 수 있으므로(실제 분석에 쓰는 /api/v1 경로가 없어도 통과됨),
        실제 분석에서 쓰는 것과 동일한 /api/v1/models 경로로 검증한다."""
        endpoint = (endpoint or "").rstrip("/")
        if not endpoint:
            return {"ok": False, "detail": "엔드포인트를 입력하세요."}
        result = self.list_lemonade_models(endpoint)
        if result.get("ok"):
            return {"ok": True, "detail": f"연결 성공 — 설치된 모델 {len(result['models'])}개 확인"}
        return {"ok": False, "detail": result.get("detail", "연결 실패")}

    def get_lemonade_health(self, endpoint):
        """lemonade-server 헬스체크 + 현재 로드된 모델 확인 (GET /api/v1/health)."""
        import urllib.request
        import json as _json
        endpoint = (endpoint or "http://localhost:13305").rstrip("/")
        try:
            with urllib.request.urlopen(f"{endpoint}/api/v1/health", timeout=5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            loaded = data.get("model_loaded") or data.get("loaded_model") or data.get("model")
            return {"ok": True, "loaded_model": loaded or None}
        except Exception as exc:
            print(f"[lemonade] health 조회 실패 endpoint={endpoint}: {exc}")
            return {"ok": False, "loaded_model": None, "detail": str(exc)}

    def unload_lemonade_model(self, endpoint):
        """lemonade-server에 현재 로드된 모델을 언로드(eject) (POST /api/v1/unload)."""
        import urllib.request
        endpoint = (endpoint or "http://localhost:13305").rstrip("/")
        try:
            req = urllib.request.Request(
                f"{endpoint}/api/v1/unload", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
            return {"ok": True}
        except Exception as exc:
            print(f"[lemonade] unload 실패 endpoint={endpoint}: {exc}")
            return {"ok": False, "detail": str(exc)}

    def load_lemonade_model(self, endpoint, model_id):
        """lemonade-server에 모델을 로드 (POST /api/v1/load)."""
        import urllib.request
        import json as _json
        endpoint = (endpoint or "http://localhost:13305").rstrip("/")
        try:
            body = _json.dumps({"model_name": model_id}).encode("utf-8")
            req = urllib.request.Request(
                f"{endpoint}/api/v1/load", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
            return {"ok": True}
        except Exception as exc:
            print(f"[lemonade] load 실패 endpoint={endpoint} model={model_id}: {exc}")
            return {"ok": False, "detail": str(exc)}

    def ensure_lemonade_model_loaded(self, endpoint, model_id, timeout_sec=90):
        """AI 분석 실행 전 호출: lemonade-server에 다른 모델이 로드돼 있으면 eject 후 선택한
        모델을 로드하고, 로드된 모델이 없으면 바로 선택한 모델을 로드한다. 이미 원하는 모델이
        로드돼 있으면 그대로 둔다. 로드가 실제로 끝날 때까지(health가 해당 모델을 보고할 때까지)
        폴링해서 대기한 뒤에만 ok=True를 반환 — 그래야 호출부가 로드 완료 후에만 분석을 시작함.

        모델 목록에 없는(실제로 설치되지 않은) model_id로 로드/채팅 완료 요청을 보내면 서버가
        404로 응답해 '연결 테스트는 성공했는데 분석은 404'로 보이는 원인이 되므로, 로드를 시도하기
        전에 실제 설치된 모델 목록에 있는지 먼저 검증한다."""
        import time
        if not model_id:
            return {"ok": False, "detail": "선택된 로컬 AI 모델이 없습니다. 설정 탭에서 모델을 선택하세요."}
        available = self.list_lemonade_models(endpoint)
        if available.get("ok") and available.get("models"):
            known_ids = {m["id"] for m in available["models"]}
            if model_id not in known_ids:
                print(f"[lemonade] 선택된 모델 '{model_id}'이(가) 서버에 설치된 목록({sorted(known_ids)})에 없음")
                return {"ok": False, "detail": (
                    f"선택된 모델 '{model_id}'이(가) 서버에 설치되어 있지 않습니다. "
                    "설정 탭에서 '모델 새로고침' 후 실제 설치된 모델을 다시 선택하세요."
                )}
        status = self.get_lemonade_health(endpoint)
        if not status.get("ok"):
            return {"ok": False, "detail": f"lemonade-server에 연결할 수 없습니다: {status.get('detail', '')}"}
        current = status.get("loaded_model")
        if current == model_id:
            return {"ok": True, "detail": f"이미 로드됨: {model_id}"}
        if current:
            unload_result = self.unload_lemonade_model(endpoint)
            if not unload_result.get("ok"):
                return {"ok": False, "detail": f"기존 모델({current}) 언로드 실패: {unload_result.get('detail', '')}"}
        load_result = self.load_lemonade_model(endpoint, model_id)
        if not load_result.get("ok"):
            return {"ok": False, "detail": f"모델({model_id}) 로드 실패: {load_result.get('detail', '')}"}
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            status = self.get_lemonade_health(endpoint)
            if status.get("ok") and status.get("loaded_model") == model_id:
                return {"ok": True, "detail": f"모델 로드 완료: {model_id}"}
            time.sleep(1)
        return {"ok": False, "detail": f"모델({model_id}) 로드 대기 시간 초과({timeout_sec}s)"}
