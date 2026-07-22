"""
클라우드 AI 제공자(Anthropic Claude, Google Gemini, OpenAI) 클라이언트 생성,
API 키 풀 관리, LLM 호출, 에러 유형 판별(인증/할당량/429 등)을 담당한다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_MODELS, _parse_local_devices

@dataclass
class KeyEntry:
    provider: str
    model: str
    client: object
    label: str          # 로그 표기용 (예: "gemini#1", "openai#1", "anthropic#1", "local-NPU")
    alive: bool = True   # False = 일일/영구 할당량 소진으로 이번 실행에서 제외
    is_local: bool = False  # True = 로컬 NPU(Lemonade). 클라우드 키가 전부 죽은 뒤에만 사용
    revive_at: float | None = None  # 할당량 소진 키의 부활 예정 시각(time.monotonic 기준). None=영구 제외


_KEY_PREFIXES = {
    "anthropic": ("sk-ant-",),
    "gemini": ("AIza", "AQ."),
    "openai": ("sk-proj-", "sk-"),  # sk- 는 openai가 마지막 순위(anthropic의 sk-ant-와 겹치므로 순서 중요)
}
_PROVIDER_ALIASES = {"gpt": "openai", "claude": "anthropic", "google": "gemini"}


def detect_provider(key: str) -> str | None:
    """
    'provider:키' 형식이면 명시적으로 그 provider를 쓴다.
    아니면 키 형태(prefix)로 자동 판별한다. sk-ant-(Anthropic) > AIza/AQ.(Gemini) > sk-(OpenAI) 순으로 검사
    (OpenAI 키가 'sk-'로 시작해 Anthropic의 'sk-ant-'와 겹치므로 Anthropic을 먼저 검사).
    """
    if ":" in key:
        prefix, rest = key.split(":", 1)
        p = _PROVIDER_ALIASES.get(prefix.strip().lower(), prefix.strip().lower())
        if p in DEFAULT_MODELS:
            return p
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith(("AIza", "AQ.")):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    return None


def strip_provider_prefix(key: str) -> str:
    if ":" in key:
        prefix, rest = key.split(":", 1)
        if _PROVIDER_ALIASES.get(prefix.strip().lower(), prefix.strip().lower()) in DEFAULT_MODELS:
            return rest.strip()
    return key


def _decode_key_file_text(raw: bytes, path: Path) -> str:
    """
    메모장 등에서 저장한 api.txt는 UTF-8이 아닐 수 있다
    (UTF-16 LE/BE + BOM이 흔함, 드물게 UTF-8 BOM이나 CP949).
    BOM으로 우선 판별하고, 없으면 순서대로 시도한다. (전체 텍스트를 반환)
    """
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = None
        for enc in ("utf-8", "utf-16", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            sys.exit(f"[오류] {path}의 인코딩을 판별할 수 없습니다. "
                     f"메모장에서 '다른 이름으로 저장 > 인코딩: UTF-8'로 다시 저장하세요.")
    return text.lstrip("\ufeff")


def load_key_pool(args) -> list[tuple[str, str]]:
    """
    api.txt(또는 --api-key-file)에서 여러 provider의 키를 한 번에 읽어
    [(provider, key), ...] 형태로 반환한다.
    한 줄에 키 하나. 형태로 provider 자동 판별(sk-ant-=anthropic, AIza/AQ.=gemini, sk-=openai).
    'gemini:AIza...'처럼 'provider:키'로 명시할 수도 있다. '#'으로 시작하는 줄은 주석.
    --provider를 명시하면 해당 provider의 키만 사용(기존 단일 provider 동작과 동일).
    파일이 없으면 환경변수(ANTHROPIC_API_KEY/GEMINI_API_KEY/OPENAI_API_KEY, 콤마로 여러 개)로 폴백.
    """
    candidates: list[Path] = []
    if args.api_key_file:
        candidates.append(Path(args.api_key_file))
    else:
        candidates += [Path.cwd() / "api.txt", Path(__file__).resolve().parent / "api.txt"]

    entries: list[tuple[str, str]] = []
    unknown = 0
    for p in candidates:
        if p.is_file():
            text = _decode_key_file_text(p.read_bytes(), p)
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                provider = detect_provider(ln)
                if provider is None:
                    unknown += 1
                    print(f"  [경고] 키 형식을 알 수 없어 건너뜀: {ln[:12]}...")
                    continue
                entries.append((provider, strip_provider_prefix(ln)))
            break  # 첫 번째로 발견된 파일만 사용

    if not entries:
        for provider, env_var in (("anthropic", "ANTHROPIC_API_KEY"),
                                  ("gemini", "GEMINI_API_KEY"),
                                  ("openai", "OPENAI_API_KEY")):
            env_val = os.environ.get(env_var) or \
                (os.environ.get("GOOGLE_API_KEY") if provider == "gemini" else None)
            if env_val:
                entries += [(provider, k.strip()) for k in env_val.split(",") if k.strip()]

    if args.provider:
        entries = [e for e in entries if e[0] == args.provider]

    if not entries:
        has_local = bool(_parse_local_devices(args))
        if has_local:
            print("[정보] 클라우드 API 키를 찾지 못함 -> 로컬 장치 지정됨, 로컬만으로 진행")
            return []
        sys.exit(
            f"[오류] API 키를 찾지 못했습니다"
            f"{f' (provider={args.provider} 필터 적용됨)' if args.provider else ''}. "
            f"다음 중 하나로 제공하세요:\n"
            f"       1) api.txt에 한 줄씩 키 추가 (형태로 provider 자동판별, 또는 'gemini:키'처럼 명시)\n"
            f"       2) export ANTHROPIC_API_KEY=... / GEMINI_API_KEY=... / OPENAI_API_KEY=... (콤마로 여러 개)\n"
            f"       3) 클라우드 키 없이 로컬만 쓰려면 --local-device npu (또는 gpu, npu,gpu)를 추가하세요"
        )
    return entries


def resolve_model(args, provider: str) -> str:
    override = {"anthropic": args.model_anthropic, "gemini": args.model_gemini,
               "openai": args.model_openai}.get(provider)
    if override:
        return override
    if args.model and args.provider == provider:
        return args.model  # 단일 provider로 명시했을 때만 --model 허용 (혼합 풀에서는 모호하므로 무시)
    return DEFAULT_MODELS[provider]


def build_client(provider: str, key: str):
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            sys.exit("[오류] anthropic SDK가 없습니다. 설치: pip install anthropic")
        return anthropic.Anthropic(api_key=key)
    if provider == "gemini":
        try:
            from google import genai
        except ImportError:
            sys.exit("[오류] google-genai SDK가 없습니다. 설치: pip install google-genai")
        return genai.Client(api_key=key)
    if provider == "openai":
        try:
            from openai import OpenAI as OpenAIClient
        except ImportError:
            sys.exit("[오류] openai SDK가 없습니다. 설치: pip install openai")
        return OpenAIClient(api_key=key)
    sys.exit(f"[오류] 알 수 없는 provider: {provider}")


def get_key_pool(args) -> list[KeyEntry]:
    """api.txt(여러 provider 혼합 가능)를 읽어 KeyEntry 풀을 구성한다."""
    raw_entries = load_key_pool(args)
    counts: dict[str, int] = {}
    pool: list[KeyEntry] = []
    for provider, key in raw_entries:
        counts[provider] = counts.get(provider, 0) + 1
        pool.append(KeyEntry(
            provider=provider,
            model=resolve_model(args, provider),
            client=build_client(provider, key),
            label=f"{provider}#{counts[provider]}",
        ))
    if pool:
        summary = ", ".join(f"{p} {n}개({resolve_model(args, p)})" for p, n in counts.items())
        print(f"[정보] API 키 {len(pool)}개 로드됨 -> {summary} "
              f"({'할당량 초과 시 순환/전환' if len(pool) > 1 else '단일 키'})")
    return pool

def call_claude(client, model: str, system_prompt: str, user_prompt: str,
                max_tokens: int, temperature: float | None) -> str:
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        msg = client.messages.create(**kwargs)
    except Exception as e:
        # 일부 신형 모델은 sampling 파라미터(temperature)를 받지 않음 -> 제거 후 1회 재시도
        if temperature is not None and "temperature" in str(e).lower():
            kwargs.pop("temperature", None)
            msg = client.messages.create(**kwargs)
        else:
            raise
    # thinking 블록 등은 .text 속성이 없으므로 자동 배제
    return "".join(getattr(block, "text", "") for block in msg.content)


def call_gemini(client, model: str, system_prompt: str, user_prompt: str,
                max_tokens: int, temperature: float | None) -> str:
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        # 응답을 JSON으로 강제 -> parse_model_json의 관대 파싱과 이중 안전장치
        response_mime_type="application/json",
    )
    if temperature is not None:
        config.temperature = temperature
    try:
        resp = client.models.generate_content(
            model=model, contents=user_prompt, config=config,
        )
    except Exception as e:
        if temperature is not None and "temperature" in str(e).lower():
            config.temperature = None
            resp = client.models.generate_content(
                model=model, contents=user_prompt, config=config,
            )
        else:
            raise
    text = getattr(resp, "text", None)
    if text:
        return text
    # candidates가 비었거나(safety 차단 등) .text가 없는 경우
    raise RuntimeError(f"Gemini 응답에 텍스트가 없습니다 (finish_reason 확인 필요): {resp}")


def call_openai(client, model: str, system_prompt: str, user_prompt: str,
                max_tokens: int, temperature: float | None) -> str:
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        # 응답을 JSON으로 강제 -> parse_model_json의 관대 파싱과 이중 안전장치
        response_format={"type": "json_object"},
    )
    if temperature is not None:
        kwargs["temperature"] = temperature

    def _fix_and_retry(e: Exception, kw: dict) -> "object":
        s = str(e).lower()
        changed = False
        if "temperature" in s and "temperature" in kw:
            kw.pop("temperature", None)
            changed = True
        if "max_tokens" in s and "max_completion_tokens" in s and "max_tokens" in kw:
            # GPT-5.x 등 신형 모델은 max_tokens 대신 max_completion_tokens 사용
            kw["max_completion_tokens"] = kw.pop("max_tokens")
            changed = True
        if not changed:
            raise e
        return client.chat.completions.create(**kw)

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        try:
            resp = _fix_and_retry(e, kwargs)
        except Exception as e2:
            # 두 파라미터가 동시에 문제였을 수 있으니 한 번 더 시도
            resp = _fix_and_retry(e2, kwargs)
    return resp.choices[0].message.content or ""


def call_llm(provider: str, client, model: str, system_prompt: str, user_prompt: str,
            max_tokens: int, temperature: float | None) -> str:
    if provider == "anthropic":
        return call_claude(client, model, system_prompt, user_prompt, max_tokens, temperature)
    if provider == "gemini":
        return call_gemini(client, model, system_prompt, user_prompt, max_tokens, temperature)
    if provider == "openai":
        return call_openai(client, model, system_prompt, user_prompt, max_tokens, temperature)
    raise ValueError(f"알 수 없는 provider: {provider}")


def parse_model_json(raw: str) -> dict[str, str]:
    """모델 출력에서 {"translations":[...]} JSON을 관대하게 추출."""
    s = raw.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # Qwen3 등 일부 로컬 모델은 <think>...</think> 사고과정 블록을 앞에 붙인다 -> 제거
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        preview = raw.strip()[:300].replace("\n", "\\n")
        raise ValueError(f"응답에서 JSON 객체를 찾지 못했습니다. 실제 응답(앞 300자): {preview!r}")
    payload = s[i:j + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        # 소형 로컬 모델이 translations 배열을 중간에 닫고 뒤 객체를 배열 밖에
        # 이어 쓰는 흔한 오류를 복구한다. JSON 전체가 깨져도 완전한 segment 객체는 회수한다.
        items = []
        obj_pat = re.compile(
            r'\{\s*"segment_id"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*'
            r'"translated_text"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}', re.DOTALL)
        for m in obj_pat.finditer(payload):
            try:
                sid = json.loads('"' + m.group(1) + '"')
                txt = json.loads('"' + m.group(2) + '"', strict=False)
                items.append({"segment_id": sid, "translated_text": txt})
            except Exception:
                continue
        if items:
            data = {"translations": items}
            print(f"    [로컬] 비정상 JSON 자동 복구: {len(items)}개 세그먼트 회수")
        else:
            try:
                data = json.loads(payload, strict=False)
            except Exception:
                preview = payload[:300].replace("\n", "\\n")
                raise ValueError(f"JSON 파싱 실패({e}). 추출된 부분(앞 300자): {preview!r}") from e
    out: dict[str, str] = {}
    for item in data.get("translations", []):
        sid, txt = item.get("segment_id"), item.get("translated_text")
        if isinstance(sid, str) and isinstance(txt, str) and txt.strip():
            out[sid] = txt
    return out


def is_rate_limit_error(e: Exception) -> bool:
    """429/할당량 초과류인지 판별. 이 경우는 일반 재시도 횟수를 소진시키지 않는다."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code == 429:
        return True
    s = str(e).lower()
    return any(kw in s for kw in (
        "429", "resource_exhausted", "rate_limit", "rate limit",
        "quota exceeded", "insufficient_quota", "too many requests",
    ))


def is_auth_error(e: Exception) -> bool:
    """
    키 자체가 잘못됐거나 차단된 '진짜 영구' 오류 (시간이 지나도 절대 안 풀림).
    이 키는 revive 대상에서 제외한다.
    """
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (401, 403):
        return True
    s = str(e).lower()
    return any(kw in s for kw in (
        "401",                          # 인증 실패 (잘못된/만료된 키)
        "unauthenticated",              # Gemini: 인증 자격 증명 오류
        "unauthorized",
        "invalid_api_key", "invalid api key", "api key not valid",
        "access_token_type_unsupported",
        "permission_denied", "denied access",  # 403: 프로젝트/키 자체가 차단됨
        "invalid authentication credentials",
        "billing",                      # 결제 관련 하드 한도 (결제 등록 전엔 안 풀림)
        "credit balance",               # Anthropic: 크레딧 부족
    ))


def is_quota_exhaustion(e: Exception) -> bool:
    """
    할당량 소진 계열 (일일 한도 등). 지금은 못 쓰지만 리셋 시간이 지나면
    다시 쓸 수 있으므로, revive_at을 설정해 나중에 자동 복귀시킨다.
    """
    s = str(e).lower()
    return any(kw in s for kw in (
        "perday",                       # Gemini: GenerateRequestsPerDayPerProjectPerModel-FreeTier 등
        "insufficient_quota",           # OpenAI: 크레딧/결제 한도 소진 (충전 시 풀리므로 재확인 가치 있음)
        "quota exceeded", "quota_exceeded",
    ))


def is_permanent_exhaustion(e: Exception) -> bool:
    """
    '이 키로는 지금 당장 계속 시도해도 안 되는' 종류인지 판별
    (인증/차단/결제 + 일일 할당량 소진 모두 포함).
    호출측에서 is_auth_error()로 진짜 영구인지, is_quota_exhaustion()으로
    시간이 지나면 부활 가능한지 구분해 revive_at을 결정한다.
    """
    return is_auth_error(e) or is_quota_exhaustion(e)


def extract_retry_delay(e: Exception, default: float = 30.0) -> float:
    """
    서버가 응답에 명시한 대기시간을 파싱한다.
      - Gemini: "...Please retry in 55.998596965s." 또는 'retryDelay': '55s'
      - Anthropic: response.headers['retry-after'] (초)
    못 찾으면 default초 대기.
    """
    s = str(e)
    m = re.search(r"retry in ([\d.]+)\s*s", s, re.IGNORECASE)
    if m:
        return float(m.group(1)) + 2.0  # 여유분 2초
    m = re.search(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]", s)
    if m:
        return float(m.group(1)) + 2.0
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if headers:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return float(ra) + 2.0
            except ValueError:
                pass
    return default


def _is_context_or_400_error(e: Exception) -> bool:
    """
    청크가 너무 커서 나는 종류의 오류인지 판별 (컨텍스트 초과, 400 Bad Request,
    413 Payload Too Large 등). 이 경우 청크를 반으로 쪼개 재시도하면 해결될 수 있다.
    """
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (400, 413, 422):
        return True
    s = str(e).lower()
    return any(kw in s for kw in (
        "400", "413", "422", "context", "too long", "too large", "maximum length",
        "token limit", "exceeds", "payload",
    ))