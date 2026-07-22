"""
로컬 AI 런타임(Lemonade / Ollama / LM Studio) 자동 기동, 헬스체크, 모델 선택,
양자화 프리셋 계산을 담당한다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import DEFAULT_LOCAL_RUNTIME, DEFAULT_MODELS, get_runtime_spec, resolve_local_model_for_device
from .providers_cloud import KeyEntry

_LOCAL_PROC = None  # 스크립트가 직접 띄운 서버 프로세스 (없으면 None = 이미 켜져 있던 것)


def local_base_url(port: int, runtime: str = DEFAULT_LOCAL_RUNTIME) -> str:
    spec = get_runtime_spec(runtime)
    return f"http://localhost:{port}{spec['api_prefix']}"


def is_local_runtime_up(port: int, runtime: str = DEFAULT_LOCAL_RUNTIME) -> bool:
    """서버가 이미 떠서 응답하는지 확인."""
    import urllib.request
    spec = get_runtime_spec(runtime)
    try:
        with urllib.request.urlopen(f"http://localhost:{port}{spec['health_path']}", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# 하위 호환용 별칭 (기존 코드/외부에서 이 이름으로 참조하던 곳 대비)
lemonade_base_url = local_base_url
is_lemonade_up = lambda port: is_local_runtime_up(port, "lemonade")
def _tail_file(path: Path, n_lines: int = 25) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n_lines:]) if lines else "(로그가 비어 있음)"
    except Exception as e:
        return f"(로그 읽기 실패: {e})"


def _try_start_one(exe: str, extra_args: list[str], port: int, args, runtime: str) -> bool:
    """한 개의 실행 명령으로 서버 기동을 시도. 성공하면 True."""
    global _LOCAL_PROC
    import subprocess

    serve_args = [exe] + list(extra_args)
    default_port = get_runtime_spec(runtime)["default_port"]
    if port != default_port and "--port" not in extra_args:
        serve_args += ["--port", str(port)]
    log_path = Path(args.input).with_suffix(f".{runtime}.log")
    print(f"[로컬] 기동 시도: {' '.join(serve_args)} (포트 {port})")

    # 한국어 Windows에서 자식 프로세스의 stdout이 파일로 리다이렉트되면 기본 인코딩이 cp949가 되어
    # (구버전) lemonade-server-dev 내부 print()의 유니코드 문자(예: '•')에서 UnicodeEncodeError로 즉시 죽는다.
    # UTF-8을 강제해 이 크래시를 막는다. (다른 런타임에도 동일하게 안전장치로 적용)
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"

    try:
        logf = open(log_path, "w", encoding="utf-8")
        creationflags = 0
        if os.name == "nt":
            # 콘솔 창을 새로 열지 않고 백그라운드로 (CREATE_NO_WINDOW), 프로세스 그룹 분리
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
        _LOCAL_PROC = subprocess.Popen(
            serve_args, stdout=logf, stderr=subprocess.STDOUT,
            creationflags=creationflags, env=child_env,
        )
    except Exception as e:
        print(f"[로컬][오류] '{exe}' 실행 자체 실패: {e}")
        return False

    print("[로컬] 서버 준비 대기 중 (최대 180초, 첫 실행은 모델 초기화로 오래 걸릴 수 있음)...")
    for _ in range(180):
        if is_local_runtime_up(port, runtime):
            print("[로컬] 서버 준비 완료")
            return True
        if _LOCAL_PROC.poll() is not None:
            print(f"[로컬][오류] '{exe}' 프로세스가 조기 종료됨 (exit code {_LOCAL_PROC.returncode}).")
            print(f"[로컬] --- 서버 로그 (마지막 25줄) ---")
            print(_tail_file(log_path))
            print(f"[로컬] --- 로그 끝 (전체: {log_path}) ---")
            _LOCAL_PROC = None
            return False
        time.sleep(1)
    print(f"[로컬][오류] 서버가 제한 시간 내에 준비되지 않음. 로그 확인: {log_path}")
    return False


def load_local_model(args) -> bool:
    """
    /api/v1/load로 모델을 명시적 컨텍스트 크기와 함께 로드한다.
    번역 엔진 프롬프트가 길기 때문에 기본 컨텍스트(보통 4096 이하)로는
    시스템 프롬프트 뒤쪽(JSON 출력 지시)이 잘려나가 모델이 JSON이 아닌 일반 텍스트로
    응답하는 문제가 생길 수 있다. 컨텍스트를 넉넉히 키워서 이를 방지한다.

    주의: FLM(FastFlowLM) 레시피는 llamacpp 전용 필드인 'ctx_size'를 받지 않고
    'flm_args'로 '--ctx-len N'을 전달해야 한다(다르면 422 Unprocessable Entity).
    모델 이름이 'FLM'을 포함하면 flm_args로, 아니면 llamacpp의 ctx_size로 시도한다.
    """
    import urllib.request
    model = args.model_local or DEFAULT_MODELS["local"]
    is_flm = "flm" in model.lower()
    if is_flm:
        payload_dict = {"model": model, "flm_args": f"--ctx-len {args.local_ctx_size}"}
    else:
        payload_dict = {"model": model, "ctx_size": args.local_ctx_size}
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{args.local_port}/api/v1/load",
        data=payload, method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            ok = r.status == 200
            if ok:
                print(f"[로컬] 모델 로드 완료: {model} (컨텍스트 {args.local_ctx_size}, "
                      f"{'flm_args' if is_flm else 'ctx_size'} 방식)")
            else:
                print(f"[로컬][경고] 모델 로드 응답 코드 {r.status} (기존 로드 설정으로 계속 진행)")
            return ok
    except Exception as e:
        # 방식이 틀렸을 수 있으니 반대 방식으로 1회 재시도
        alt_dict = {"model": model, "ctx_size": args.local_ctx_size} if is_flm \
            else {"model": model, "flm_args": f"--ctx-len {args.local_ctx_size}"}
        try:
            alt_req = urllib.request.Request(
                f"http://localhost:{args.local_port}/api/v1/load",
                data=json.dumps(alt_dict).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(alt_req, timeout=120) as r:
                if r.status == 200:
                    print(f"[로컬] 모델 로드 완료 (대체 방식: {list(alt_dict.keys())[-1]})")
                    return True
        except Exception:
            pass
        print(f"[로컬][경고] 명시적 컨텍스트 로드 실패, 기존 로드 상태로 계속 진행: {e}")
        return False


def ensure_local_runtime(args) -> bool:
    """
    로컬 AI 런타임(args.local_runtime)이 떠 있으면 재사용, 없으면 백그라운드로 자동 기동한다.
    RUNTIME_REGISTRY에 등록된 실행 명령 후보를 순서대로 시도하고, 하나라도 성공하면 True.
    Lemonade 전용 부가 작업(컨텍스트 명시 로드, 모델 선택 메뉴)은 그 런타임일 때만 수행한다.
    Ollama/LM Studio 등은 사용자가 이미 설치해서 모델까지 받아둔 상태를 그대로 재사용하는
    쪽이 기본 전제이므로, 자동 기동은 시도하되 실패하면 "수동으로 켜두라"고 안내하고 넘어간다.
    """
    runtime = getattr(args, "local_runtime", DEFAULT_LOCAL_RUNTIME)
    spec = get_runtime_spec(runtime)
    port = args.local_port

    def _after_up():
        if spec.get("needs_model_menu"):
            prepare_local_model(args)
        elif spec.get("needs_ctx_load"):
            load_local_model(args)
        # 그 외(Ollama/LM Studio 등)는 별도 후처리 없음 - 사용자가 그쪽 앱/CLI로 이미 모델을
        # 받아 로드해뒀다고 가정한다.

    if is_local_runtime_up(port, runtime):
        print(f"[로컬] 이미 실행 중인 {spec['label']} 감지 (포트 {port}) -> 재사용")
        _after_up()
        return True

    if args.local_serve_cmd:
        candidates = [(args.local_serve_cmd, [])]
    else:
        candidates = list(spec["serve_candidates"])
    found = [(exe, extra) for exe, extra in candidates if shutil.which(exe)]
    if not found:
        names = ", ".join(c[0] for c in candidates)
        print(f"[로컬][오류] {spec['label']} 실행파일을 찾지 못했습니다 (시도: {names}).")
        print(f"[로컬] 해결책: 별도 터미널에서 {spec['label']}를 먼저 켜두면 "
              f"이 스크립트가 포트 {port}에서 자동 감지해 재사용합니다.")
        return False

    for exe, extra in found:
        if _try_start_one(exe, extra, port, args, runtime):
            _after_up()
            return True
        # 이 후보가 실패했지만 그 사이 다른 프로세스가 포트를 잡았을 수도 있으니 재확인
        if is_local_runtime_up(port, runtime):
            print(f"[로컬] 서버가 포트 {port}에 떠 있음 -> 재사용")
            _after_up()
            return True

    print(f"[로컬][오류] 모든 기동 명령({', '.join(c[0] for c in found)})이 실패했습니다.")
    print(f"[로컬] 위 서버 로그를 확인하세요. 흔한 원인:")
    print(f"        - 포트 {port} 이미 사용 중 -> 그 서버를 재사용해야 하는데 감지 실패")
    print(f"        - 메모리 부족(다른 앱, 특히 브라우저를 닫고 재시도)")
    print(f"        - 해결이 안 되면 수동으로 {spec['label']}를 먼저 켜두고 이 스크립트를 다시 실행")
    return False


# 하위 호환용 별칭
ensure_lemonade_server = ensure_local_runtime


def shutdown_local_runtime():
    """스크립트가 직접 띄운 서버만 종료 (원래 켜져 있던 건 건드리지 않음)."""
    global _LOCAL_PROC
    if _LOCAL_PROC is not None:
        print("[로컬] 자동 기동한 로컬 서버 종료")
        try:
            _LOCAL_PROC.terminate()
            _LOCAL_PROC.wait(timeout=10)
        except Exception:
            try:
                _LOCAL_PROC.kill()
            except Exception:
                pass
        _LOCAL_PROC = None


# 하위 호환용 별칭
shutdown_lemonade_server = shutdown_local_runtime


def make_local_entry(args, device: str = "npu") -> KeyEntry:
    """로컬 NPU/GPU용 KeyEntry 생성 (OpenAI 호환 클라이언트를 로컬 엔드포인트로 지정).
    device는 로그 라벨 구분용("local-NPU"/"local-GPU")이며 실제 호출 경로는 동일하다
    (같은 런타임 서버가 내부적으로 NPU/GPU 중 뭘 쓸지는 그 런타임이 알아서 결정)."""
    from openai import OpenAI as OpenAIClient
    runtime = getattr(args, "local_runtime", DEFAULT_LOCAL_RUNTIME)
    model = resolve_local_model_for_device(args, device)
    client = OpenAIClient(base_url=local_base_url(args.local_port, runtime), api_key="local",
                          timeout=args.local_timeout, max_retries=0)
    label = f"local-{device.upper()}({get_runtime_spec(runtime)['label']})"
    return KeyEntry(provider="openai", model=model, client=client,
                    label=label, is_local=True)


# ---------------------------------------------------------------------------
# 로컬 모델 선택 메뉴 + 양자화 프리셋
# ---------------------------------------------------------------------------
# 양자화 비트별 로컬 처리 파라미터 (낮은 비트 = 낮은 품질/불안정 -> 작게 나눠서 실패 파장 축소)
_QUANT_PRESETS = {
    2: {"batch_chars": 450,  "batch_segs": 2,  "max_tokens": 3072},
    4: {"batch_chars": 1000, "batch_segs": 5,  "max_tokens": 4096},
    8: {"batch_chars": 2500, "batch_segs": 16, "max_tokens": 6144},
}
_QUANT_RE = re.compile(r'(?:^|[-_.])(?:e(?P<eb>[2-8])b|(?P<plain>[2-8])b|q(?P<q>[2-8])(?:[_-]\d+)?|int(?P<int>[2-8]))(?:$|[-_.])', re.IGNORECASE)


def detect_quant_bits(model_name: str) -> int | None:
    """모델명에서 e2b/e4b, 2b/4b/8b, q4_1, int4 형식의 양자화 비트를 감지한다."""
    m = _QUANT_RE.search(model_name)
    if not m:
        return None
    for key in ("eb", "plain", "q", "int"):
        if m.groupdict().get(key):
            return int(m.group(key))
    return None


def local_presets_for(model_name: str) -> dict:
    """
    양자화 비트에 따른 배치/토큰 프리셋 반환. 2/4/8비트는 고정값,
    그 사이 비트(3,5,6,7)는 인접 두 프리셋의 선형 보간.
    감지 실패 시 4비트 프리셋 사용 (FLM 기본 양자화가 Q4_1이므로 합리적 기본값).
    """
    bits = detect_quant_bits(model_name)
    if bits is None:
        bits = 4
        print(f"[로컬] 모델명에서 양자화 비트를 감지하지 못함 -> 4비트 프리셋 사용 (FLM 기본 Q4 가정)")
    if bits in _QUANT_PRESETS:
        preset = dict(_QUANT_PRESETS[bits])
    else:
        # 선형 보간 (예: 3비트 = 2비트와 4비트의 중간)
        lo = max(b for b in _QUANT_PRESETS if b < bits)
        hi = min(b for b in _QUANT_PRESETS if b > bits)
        t = (bits - lo) / (hi - lo)
        preset = {k: int(_QUANT_PRESETS[lo][k] + t * (_QUANT_PRESETS[hi][k] - _QUANT_PRESETS[lo][k]))
                  for k in _QUANT_PRESETS[lo]}
    print(f"[로컬] 양자화 {bits}비트 프리셋 적용: 청크 {preset['batch_chars']}자/"
          f"{preset['batch_segs']}세그먼트, max_tokens {preset['max_tokens']}")
    return preset


def timed_input(prompt: str, timeout: float) -> str | None:
    """
    timeout초 안에 입력이 없으면 None 반환. Windows(msvcrt)와 POSIX(select) 모두 지원.
    """
    print(prompt, end="", flush=True)
    if os.name == "nt":
        import msvcrt
        buf = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwche()
                if ch in ("\r", "\n"):
                    print()
                    return buf.strip()
                elif ch == "\b":
                    buf = buf[:-1]
                else:
                    buf += ch
            time.sleep(0.05)
        print()
        return None
    else:
        import select
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
        if rlist:
            return sys.stdin.readline().strip()
        print()
        return None


def fetch_local_models(port: int) -> list[str]:
    """서버의 /api/v1/models에서 사용 가능한 모델 ID 목록을 가져온다."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/v1/models", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = data.get("data", data if isinstance(data, list) else [])
        ids = []
        for it in items:
            mid = it.get("id") if isinstance(it, dict) else str(it)
            if mid:
                ids.append(mid)
        return ids
    except Exception as e:
        print(f"[로컬][경고] 모델 목록 조회 실패: {e}")
        return []


def fetch_loaded_local_model(port: int) -> str | None:
    """Lemonade 모델 목록 응답에서 현재 로드/실행 중인 모델을 최대한 보수적으로 감지한다."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/v1/models", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = data.get("data", data if isinstance(data, list) else [])
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = it.get("id") or it.get("model")
            status = str(it.get("status", "")).lower()
            loaded = it.get("loaded") is True or it.get("is_loaded") is True
            active = it.get("active") is True or it.get("is_active") is True
            if mid and (loaded or active or status in {"loaded", "running", "active", "ready"}):
                return str(mid)
        # 일부 Lemonade 버전은 최상위 필드로 현재 모델을 반환할 수 있음
        for key in ("loaded_model", "active_model", "model"):
            if isinstance(data, dict) and data.get(key):
                return str(data[key])
    except Exception as e:
        print(f"[로컬][경고] 로드된 모델 감지 실패: {e}")
    return None


def prepare_local_model(args) -> None:
    """로드된 모델을 최우선 기본값으로 선택하고, 필요할 때만 모델을 로드한다."""
    loaded_model = fetch_loaded_local_model(args.local_port)
    if loaded_model:
        print(f"[로컬] 현재 로드된 모델 감지: {loaded_model} -> 최우선 기본값")
        args.model_local = loaded_model
    selected = choose_local_model(args)
    args.model_local = selected
    if loaded_model == selected:
        print(f"[로컬] 이미 로드된 모델 재사용: {selected}")
        return
    load_local_model(args)


def choose_local_model(args) -> str:
    """
    서버의 모델 목록을 번호로 보여주고, 제한시간 안에 번호 입력이 없으면 기본 모델로 진행.
    """
    default_model = args.model_local or DEFAULT_MODELS["local"]
    if getattr(args, "model_select_timeout", 10.0) <= 0:
        # GUI 등에서 모델을 이미 지정한 경우: 메뉴 없이 즉시 진행 (stdin 없는 환경 대응)
        return default_model
    models = fetch_local_models(args.local_port)
    if not models:
        print(f"[로컬] 모델 목록을 못 가져와 기본 모델로 진행: {default_model}")
        return default_model

    # 기본 모델을 목록 맨 위로
    if default_model in models:
        models.remove(default_model)
        models.insert(0, default_model)

    print(f"[로컬] 사용 가능한 모델 (기본값: 1번 {default_model}):")
    for i, m in enumerate(models, 1):
        marker = " (기본값)" if m == default_model else ""
        print(f"  {i}. {m}{marker}")
    ans = timed_input(f"[로컬] 번호 입력 ({args.model_select_timeout:.0f}초 내 미입력 시 기본값): ",
                      args.model_select_timeout)
    if ans:
        try:
            idx = int(ans)
            if 1 <= idx <= len(models):
                print(f"[로컬] 선택됨: {models[idx - 1]}")
                return models[idx - 1]
        except ValueError:
            # 번호가 아니라 모델명을 직접 친 경우도 허용
            if ans in models:
                print(f"[로컬] 선택됨: {ans}")
                return ans
        print(f"[로컬] 잘못된 입력 '{ans}' -> 기본값으로 진행")
    else:
        print(f"[로컬] 입력 없음 -> 기본값으로 진행: {default_model}")
    return default_model