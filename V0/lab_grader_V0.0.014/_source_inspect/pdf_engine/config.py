"""
버전, 경로 상수, 실행 중단(STOP) 제어, 로컬 런타임 레지스트리, 기본 모델 설정.
다른 모든 모듈이 가장 먼저 의존하는 기반 모듈이라 여기엔 다른 pdf_engine 모듈을
import하지 않는다(순환 참조 방지).
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

# 버전 표기는 이 상수 하나에서만 관리한다. gui.py는 이 값을 import해서 타이틀에 쓰고,
# build_exe.bat은 이 값을 읽어 실행파일 이름을 결정한다 (버전 문자열이 여러 곳에 흩어져
# 서로 어긋나는 사고 방지 - 예: v3.82로 하드코딩된 채 zip 이름만 v3.84로 배포됐던 문제).
__version__ = "5.7"

# ---------------------------------------------------------------------------
# 로컬 AI 런타임 레지스트리 - Lemonade 외 다른 로컬 서버(Ollama, LM Studio 등)를
# 지원하기 위한 확장 지점.
# ---------------------------------------------------------------------------
RUNTIME_REGISTRY: dict[str, dict] = {
    "lemonade": {
        "label": "Lemonade Server",
        "default_port": 13305,
        "supports_npu": True,
        "supports_gpu": True,
        "api_prefix": "/api/v1",       # base_url = http://localhost:{port}{api_prefix}
        "health_path": "/api/v1/models",
        "serve_candidates": [("LemonadeServer", []), ("lemonade-server-dev", ["serve"])],
        "needs_ctx_load": True,        # /api/v1/load로 컨텍스트 크기 명시 필요 (긴 프롬프트 대응)
        "needs_model_menu": True,      # 번호 선택 메뉴 지원 (choose_local_model)
    },
    "ollama": {
        "label": "Ollama",
        "default_port": 11434,
        "supports_npu": False,
        "supports_gpu": True,
        "api_prefix": "/v1",
        "health_path": "/v1/models",
        "serve_candidates": [("ollama", ["serve"])],
        "needs_ctx_load": False,
        "needs_model_menu": False,
    },
    "lmstudio": {
        "label": "LM Studio",
        "default_port": 1234,
        "supports_npu": False,
        "supports_gpu": True,
        "api_prefix": "/v1",
        "health_path": "/v1/models",
        # LM Studio는 보통 앱을 켜두거나 'lms server start'로 미리 띄워두는 걸 전제로 한다.
        # CLI 자동 기동은 이 환경에서 검증되지 않았으니 실패해도 "수동으로 켜두라"고 안내한다.
        "serve_candidates": [("lms", ["server", "start"])],
        "needs_ctx_load": False,
        "needs_model_menu": False,
    },
}

DEFAULT_LOCAL_RUNTIME = "lemonade"

def get_runtime_spec(name: str) -> dict:
    spec = RUNTIME_REGISTRY.get(name)
    if spec is None:
        valid = ", ".join(RUNTIME_REGISTRY)
        raise SystemExit(f"[오류] 알 수 없는 --local-runtime '{name}'. 사용 가능: {valid}")
    return spec
SCRIPT_DIR = Path(__file__).resolve().parent.parent  # translate_pdf.py와 같은 위치 (pdf_engine/의 부모)

STOP_EVENT = threading.Event()

def request_stop() -> None:
    STOP_EVENT.set()

def reset_stop() -> None:
    STOP_EVENT.clear()

def stop_requested() -> bool:
    return STOP_EVENT.is_set()
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "prompts" / "system_prompt.txt"
SYSTEM_PROMPT_LOCAL_PATH = SCRIPT_DIR / "prompts" / "system_prompt_local.txt"
USER_TEMPLATE_PATH = SCRIPT_DIR / "prompts" / "user_template.txt"

LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-5.4-mini",
    "local": "gemma4-it-e2b-FLM",  # 하위 호환용 (device 미지정 시 기본값 = NPU 모델)
}
DEFAULT_MODEL = DEFAULT_MODELS["anthropic"]  # 하위 호환용

LEMONADE_DEFAULT_PORT = 13305  # 신버전(C++) Lemonade Server 기본 포트 (구버전 Python 서버는 8000이었음)
LEMONADE_SERVE_CMD = "LemonadeServer"  # 신버전(C++) 실제 서버 실행파일. 'lemonade'는 그 서버에 말 거는 클라이언트일 뿐 서버 본체가 아님
DEFAULT_TERMINOLOGY_POLICY = (
    "Use established target-language technical terminology. "
    "Keep well-known technical abbreviations, protocol names, commands, and product names "
    "(e.g., VXLAN, BGP, OSPF, CLI, API) in their original form. "
    "For an important technical term, the first occurrence may include the original term in parentheses."
)
# ---------------------------------------------------------------------------
# 로컬 NPU/GPU 장치별 기본 모델 (Lemonade에서 NPU/GPU는 모델의 recipe로 고정됨)
# ---------------------------------------------------------------------------
DEFAULT_LOCAL_MODEL_BY_DEVICE = {
    "npu": "gemma4-it-e2b-FLM",       # FLM 레시피 -> XDNA NPU 전용
    "gpu": "Gemma-3-4b-it-GGUF",      # llamacpp 레시피 -> Vulkan/ROCm/CUDA로 GPU 사용
}


def model_recipe_device(model_name: str) -> str:
    """모델 이름으로 실제 실행 장치를 추정. 'FLM' 접미사 = NPU 전용, 그 외 = GPU/CPU(llamacpp)."""
    return "npu" if model_name.lower().endswith("-flm") or "-flm-" in model_name.lower() \
        else "gpu"


def resolve_local_model_for_device(args, device: str) -> str:
    """
    device별 전용 인자(--model-local-npu/--model-local-gpu)가 있으면 최우선,
    없으면 공용 --model-local, 그것도 없으면 device 기본값을 쓴다.
    명시된 모델이 요청한 device와 실제로 안 맞으면 경고한다(예: device=gpu인데 -FLM 모델을
    지정한 경우 -> 여전히 NPU로 돌 것이라는 사실을 알려줌).
    """
    per_device = getattr(args, f"model_local_{device}", None)
    explicit = per_device or args.model_local
    if explicit:
        actual = model_recipe_device(explicit)
        if actual != device:
            print(f"[경고] {explicit}은(는) 실제로 {actual.upper()} 전용 모델입니다. "
                  f"{device.upper()}를 요청했지만 이 모델은 항상 {actual.upper()}로 실행됩니다 "
                  f"(Lemonade는 장치를 모델의 recipe로 고정하며, 실행 시점에 전환할 수 없습니다).")
        return explicit
    return DEFAULT_LOCAL_MODEL_BY_DEVICE.get(device, DEFAULT_MODELS["local"])


def _parse_local_devices(args) -> list[str]:
    """--local-device/--local-npu/--no-local-npu를 종합해 사용할 장치 순서 목록을 만든다."""
    if getattr(args, "no_local_npu", False):
        return []
    if getattr(args, "local_device", None):
        devices = [d.strip().lower() for d in args.local_device.split(",") if d.strip()]
        valid = {"npu", "gpu"}
        bad = [d for d in devices if d not in valid]
        if bad:
            raise SystemExit(f"[오류] --local-device에 알 수 없는 장치: {bad} (사용 가능: npu, gpu)")
        return devices
    if getattr(args, "local_npu", False):
        return ["npu"]
    return []