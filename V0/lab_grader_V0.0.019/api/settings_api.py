"""SettingsApiMixin — AI 제공자 우선순위(드래그 재정렬) 저장/조회."""
import os
import uuid

DEFAULT_AI_ORDER = [
    {"id": "cloud_apis", "label": "클라우드 API", "desc": "등록된 API 키를 체크된 순서대로 시도", "icon": "cloud"},
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
        local["endpoint"] = settings.get("endpoint", "http://localhost:13305")
        local["model"] = settings.get("model", self.LOCAL_MODEL_CHOICES[0]["id"])
        try:
            local["temperature"] = max(0.0, min(2.0, float(settings.get("temperature", 0.3))))
        except (TypeError, ValueError):
            local["temperature"] = 0.3
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

# ---------- 클라우드 API 설정 ----------
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

    def get_cloud_apis(self):
        """클라우드 API 설정 반환 (모델 목록 포함)."""
        cfg = self._load_ai_config()
        node = next((p for p in cfg.get("providers", []) if p.get("type") == "cloud_apis"), {"entries": []})
        return {
            "provider_types": self.CLOUD_PROVIDER_TYPES,
            "entries": [
                {
                    "id": e.get("id"),
                    "name": e.get("name", ""),
                    "provider": e.get("provider", "anthropic"),
                    "model": e.get("model", ""),  # 모델 데이터 추가
                    "enabled": bool(e.get("enabled")),
                    "has_key": bool(e.get("api_key")),
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
        
        # Provider별 첫 번째(1순위) 모델을 기본값으로 할당
        default_model = ""
        for p in self.CLOUD_PROVIDER_TYPES:
            if p["id"] == provider and "models" in p:
                default_model = p["models"][0]
                break

        node["entries"].append({
            "id": entry_id,
            "name": (name or "").strip() or f"{provider}-{entry_id[:4]}",
            "provider": provider,
            "model": default_model,
            "api_key": key,
            "enabled": bool(key),
        })
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return {"ok": True, "id": entry_id}

    def remove_cloud_api(self, entry_id):
        import yaml
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        node["entries"] = [e for e in node["entries"] if e.get("id") != entry_id]
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return {"ok": True}

    def update_cloud_api(self, entry_id, fields):
        """name / provider / enabled / api_key / model 수정."""
        import yaml
        cfg = self._load_ai_config()
        node = self._get_cloud_apis_provider(cfg)
        entry = next((e for e in node["entries"] if e.get("id") == entry_id), None)
        if entry is None:
            return {"error": "항목을 찾을 수 없습니다."}
            
        if "name" in fields and fields["name"] is not None:
            entry["name"] = fields["name"].strip() or entry["name"]
        if "provider" in fields and fields["provider"] in [p["id"] for p in self.CLOUD_PROVIDER_TYPES]:
            entry["provider"] = fields["provider"]
        if "model" in fields:  # 모델 업데이트 처리 로직 추가
            entry["model"] = fields["model"].strip()
        if "enabled" in fields:
            entry["enabled"] = bool(fields["enabled"])
        if fields.get("api_key"):
            entry["api_key"] = fields["api_key"].strip()
            
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
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

        # Provider별 기본 청크 문자 수치 초기 할당
        default_batch_chars = 30000
        if provider == "gemini":
            default_batch_chars = 40000
        elif provider == "anthropic":
            default_batch_chars = 20000

        node["entries"].append({
            "id": entry_id,
            "name": (name or "").strip() or f"{provider}-{entry_id[:4]}",
            "provider": provider,
            "model": default_model,
            "api_key": key,
            "enabled": bool(key),
            "batch_chars": default_batch_chars,
            "max_tokens": 1500
        })
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
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
        if "model" in fields:
            entry["model"] = fields["model"].strip()
        if "enabled" in fields:
            entry["enabled"] = bool(fields["enabled"])
        if fields.get("api_key"):
            entry["api_key"] = fields["api_key"].strip()

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
        
        with open(self.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return {"ok": True}

    # ---------- 터미널 UI(우클릭 동작) ----------
    TERMINAL_UI_PATH = "config/terminal_ui.yaml"

    def get_terminal_ui_settings(self):
        import yaml
        if not os.path.exists(self.TERMINAL_UI_PATH):
            return {"context_menu_mode": "menu"}
        with open(self.TERMINAL_UI_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return {"context_menu_mode": cfg.get("context_menu_mode", "menu")}

    def save_terminal_ui_settings(self, context_menu_mode):
        import yaml
        if context_menu_mode not in ("menu", "paste"):
            return {"error": f"알 수 없는 모드: {context_menu_mode}"}
        os.makedirs(os.path.dirname(self.TERMINAL_UI_PATH), exist_ok=True)
        with open(self.TERMINAL_UI_PATH, "w", encoding="utf-8") as f:
            yaml.dump({"context_menu_mode": context_menu_mode}, f, allow_unicode=True, sort_keys=False)
        return {"ok": True}
