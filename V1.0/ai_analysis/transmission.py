"""외부 AI 전송 미리보기·승인 — IMPROVEMENT_PLAN 4-2(결정 D3: 클라우드는 강제 마스킹과 미리보기 승인을 거친 뒤에만 쓴다).

흐름: 화면이 미리보기를 청하면 여기서 '보낼 내용'(파일마다 가린 뒤 나눈 프롬프트)을 만들어 서버 메모리에
미리보기 id 로 맡겨 두고, 화면에는 받는 곳·키 순서·요청 수·글자 수를 보여 준다(파일별 내용은 따로 받아 본다).
사용자가 승인하면 화면은 미리보기 id 만 돌려보내고, 서버는 맡겨 둔 바로 그 내용만 보낸다 — 화면이 내용을
고쳐 보낼 수 없고, 미리보기를 본 뒤 로그가 바뀌어도 승인하지 않은 내용은 나가지 않는다. 승인과 끝난 결과는
ai_transmissions.jsonl 에 한 줄씩 남긴다(보낸 내용은 남기지 않고 SHA-256 만).

키 로테이션: 사용 체크된 첫 항목과 같은 곳(같은 제공자·같은 주소)으로 가는 뒤 항목들만 돌려 쓴다. 다른
제공자로는 넘어가지 않는다 — 승인한 곳 밖으로 데이터가 가면 안 된다.

이 PC 안의 로컬 AI(loopback)는 데이터가 밖으로 나가지 않으므로 승인 없이 바로 분석한다(호출부가 정한다).
"""
import getpass
import hashlib
import json
import os
import threading
import time
import uuid
from urllib.parse import urlparse

from ai_analysis import raw_log_analyzer
from core import cloud_providers
from core.app_settings import ai_transmission_log_path
from core.log_naming import device_from_log_name, stamp_or_unknown
from core.secret_redaction import redact_secrets
from core.utils.datetime import format_iso
from engine.log_masking import EXTERNAL_AI_KEYS, MASK_KEY_ORDER, MASK_RULES, find_unmasked_secrets

PREVIEW_TTL_SEC = 600
NO_FINDINGS_TEXT = "이 로그에서는 이상 징후를 발견하지 못했습니다."

_RECORD_LOCK = threading.Lock()


# ---------- 어디로 보내는가 ----------
def _cloud_entries(cfg):
    for provider in (cfg or {}).get("providers") or []:
        if isinstance(provider, dict) and provider.get("type") == "cloud_apis":
            for entry in provider.get("entries") or []:
                if isinstance(entry, dict):
                    yield entry


def _provider(entry):
    return entry.get("provider") or "anthropic"


def cloud_key_ring(cfg):
    """클라우드 분석에 쓸 항목들 — 사용 체크·키가 있는 첫 항목과, 그 뒤에서 같은 곳(제공자·주소)으로 가는 항목들."""
    entries = [e for e in _cloud_entries(cfg) if e.get("enabled") and e.get("api_key")]
    if not entries:
        return []
    primary = entries[0]

    def goes_to(entry):
        return _provider(entry), entry.get("endpoint") or ""

    return [primary] + [e for e in entries[1:] if goes_to(e) == goes_to(primary)]


def destination(ai_mode, ring):
    """받는 곳 요약 — 화면과 승인 이력이 쓴다(키 값은 넣지 않는다)."""
    primary = ring[0]
    if ai_mode == "local":
        endpoint = primary.get("endpoint") or "http://localhost:13305"
        return {"provider": "local", "label": "로컬 AI 서버", "host": urlparse(endpoint).hostname or "",
                "model": primary.get("model") or "", "keys": [], "fallback": False}
    provider = _provider(primary)
    spec = cloud_providers.get(provider) or {}
    keys = [{"name": e.get("name") or provider, "model": e.get("model") or cloud_providers.default_model(provider)}
            for e in ring]
    return {
        "provider": provider,
        "label": spec.get("label", provider),
        "host": cloud_providers.host_of(primary),
        "model": keys[0]["model"],
        "keys": keys,
        # 안전 분류기가 거절하면 Anthropic 이 다른 Claude 모델로 다시 처리할 수 있다(raw_log_analyzer 참고).
        "fallback": (cloud_providers.wire_of(provider) == "anthropic"
                     and any(k["model"] in cloud_providers.ANTHROPIC_FALLBACK_MODELS for k in keys)),
    }


def ring_signature(ai_mode, ring):
    """승인한 받는 곳과 지금 설정이 같은지 맞춰 보는 값 — 키 값은 빼고 항목·제공자·모델·주소만."""
    if ai_mode == "local":
        return [("local", (ring[0].get("model") or ""), (ring[0].get("endpoint") or ""))]
    return [(e.get("id"), _provider(e), e.get("model") or "", e.get("endpoint") or "") for e in ring]


# ---------- 무엇을 보내는가 ----------
def _local_payload(source, context_text, is_virtual):
    """로컬 AI용 경량화 구조화 템플릿(토큰 페이로드 축소 및 500 오류/타임아웃 방지)."""
    if is_virtual:
        platform = "Arista vEOS-lab (Virtual Platform)"
        task = ("Focus strictly on operational failures (Reloads, Interface/MLAG down, "
                "BGP/EVPN, STP changes). Ignore expected virtual limitations "
                "(show module/environment unavailable).")
    else:
        platform = "Physical network switch (production hardware)"
        task = ("Report hardware faults (power/fan/temperature/module/transceiver) as well as "
                "operational failures (Reloads, Interface/MLAG down, BGP/EVPN, STP changes).")
    return (
        f"[DEVICE_NAME]: {source}\n"
        f"[PLATFORM]: {platform}\n"
        f"[FILTERED_PRE_CHECK_OUTPUT]:\n{context_text}\n"
        f"[ANALYSIS_TASK]: {task}"
    )


def neighbor_names(devices):
    """장비 목록 -> 외부로 나갈 때 함께 가릴 장비명. 이웃 장비는 그 로그의 장비가 아니라서 이름을 따로 알려 줘야
    한다 — 인터페이스 설명('to Agg1')·LLDP 이웃 표·BGP 설명에 나온다. 한 글자이거나 숫자뿐인 이름은 뺀다: 낱말
    단위로 가리므로 '12' 는 로그의 모든 12 를, 'R' 은 라우팅 표의 R 코드를 지워 분석을 망친다."""
    names = []
    for device in devices or []:
        name = str(device.get("name") or "").strip()
        if len(name) >= 2 and any(ch.isalpha() for ch in name) and name not in names:
            names.append(name)
    return tuple(names)


def build_plan(paths, ai_mode, api_cfg, is_virtual, known_names=()):
    """분석할 원본 로그들 -> 파일마다 보낼 내용. 미리보기와 실제 분석이 이 함수 하나를 쓴다.

    원문 전체가 아니라 의심되는 블록 주위 텍스트만 보낸다(비용·품질). 판정은 규칙기반 분석/대시보드가 이미
    했을 수 있으므로 캐시된 것을 넘긴다. 장비명(파일명에서 얻은 것)은 결과 파일명에도 쓰고, 외부로 나갈 때
    가릴 이름으로도 쓴다. known_names(장비 목록의 장비명, neighbor_names)도 함께 가린다.
    의심 구간이 없는 파일은 보내지 않는다(requests=None)."""
    from engine.log_analysis import extract_suspicious_context
    from engine.log_cache import cached_findings, cached_log_text

    prefix = "LocalAI" if ai_mode == "local" else "CloudAI"
    plan = []
    for path in paths:
        raw_text = cached_log_text(path)
        device = device_from_log_name(path)
        source = os.path.basename(path)
        context_text = extract_suspicious_context(raw_text, findings=cached_findings(path, text=raw_text))
        requests = None
        if context_text.strip():
            # 클라우드는 의심 구간을 그대로(프롬프트 머리말은 build_prompts 가 붙인다), 로컬은 구조화 템플릿으로.
            payload = _local_payload(source, context_text, is_virtual) if ai_mode == "local" else context_text
            requests = raw_log_analyzer.build_prompts(payload, ai_mode, api_cfg, is_virtual=is_virtual,
                                                      device_names=(device, source, *known_names))
        plan.append({
            "source": source,
            "device": device,
            "out_name": f"{prefix}_{stamp_or_unknown(path)}_{device}_problems.txt",
            "requests": requests,
        })
    return plan


def payload_digest(plan):
    """보낼 내용 전체의 SHA-256 — 승인 이력에 남겨, 나중에 같은 내용인지 맞춰 볼 수 있게."""
    digest = hashlib.sha256()
    for item in plan:
        for prompt in item["requests"].prompts if item["requests"] else ():
            digest.update(prompt.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def masking_labels(known_names=0):
    """외부로 나갈 때 사용자 선택과 무관하게 항상 가리는 항목 — 화면에 보여 준다."""
    keys = [key for key in MASK_KEY_ORDER if key in EXTERNAL_AI_KEYS]
    keys += sorted(EXTERNAL_AI_KEYS - set(MASK_KEY_ORDER))
    return [MASK_RULES[key]["label"] for key in keys] + [f"Device Name (장비 목록 {known_names}개·파일명·프롬프트)"]


def preview_view(preview_id, preview):
    """화면에 보낼 미리보기 요약 — 파일별 내용은 넣지 않는다(수 MB 가 될 수 있다. part_text 로 따로 본다)."""
    head = raw_log_analyzer.prompt_prefix(preview.get("is_virtual", False))
    files, skipped = [], []
    for index, item in enumerate(preview["plan"]):
        requests = item["requests"]
        if not requests:
            skipped.append(item["source"])
            continue
        # 마스킹이 놓친 비밀값이 남았는지 한 번 더 본다 — 머리말(지시문)은 빼고 로그 부분만.
        body = "\n".join(p[len(head):] if p.startswith(head) else p for p in requests.prompts)
        found = find_unmasked_secrets(body)
        files.append({
            "index": index,
            "source": item["source"],
            "requests": len(requests.prompts),
            "chars": sum(len(p) for p in requests.prompts),
            "unmasked": [f"{MASK_RULES[key]['label']} {count}건" for key, count in found.items()],
        })
    return {
        "needs_approval": True,
        "preview_id": preview_id,
        "mode": preview["mode"],
        "customer": preview.get("customer") or "",
        "profile": preview.get("profile") or "",
        "destination": preview["destination"],
        "masking": masking_labels(preview.get("known_names", 0)),
        "warnings": list(preview.get("warnings") or []),
        "files": files,
        "skipped": skipped,
        "totals": {
            "files": len(files),
            "requests": sum(f["requests"] for f in files),
            "chars": sum(f["chars"] for f in files),
        },
        "expires_in_sec": PREVIEW_TTL_SEC,
    }


def part_text(item):
    """파일 하나가 실제로 보낼 프롬프트 전체(요청이 여러 건이면 구분선을 넣어 잇는다)."""
    prompts = item["requests"].prompts if item["requests"] else ()
    if len(prompts) == 1:
        return prompts[0]
    return "\n\n".join(f"──── 요청 {n}/{len(prompts)} ────\n{prompt}" for n, prompt in enumerate(prompts, start=1))


# ---------- 미리보기 보관 ----------
class PreviewStore:
    """미리보기 id -> 맡겨 둔 내용. 모드마다 마지막 하나만 두고(새 미리보기를 만들면 앞의 것은 승인할 수 없다),
    PREVIEW_TTL_SEC 가 지나면 버린다. 승인에 한 번 쓰면 지운다 — 같은 승인으로 두 번 보내지 않는다."""

    def __init__(self, ttl=PREVIEW_TTL_SEC, clock=time.monotonic):
        self._lock = threading.Lock()
        self._items = {}
        self._ttl = ttl
        self._clock = clock

    def _alive(self, slot):
        return self._clock() - slot["created"] < self._ttl

    def put(self, mode, preview):
        preview_id = uuid.uuid4().hex
        with self._lock:
            self._items = {pid: slot for pid, slot in self._items.items()
                           if slot["mode"] != mode and self._alive(slot)}
            self._items[preview_id] = {"mode": mode, "created": self._clock(), "preview": preview}
        return preview_id

    def get(self, preview_id):
        """보기만 한다(내용 보기) — 없거나 지났으면 None."""
        with self._lock:
            slot = self._items.get(preview_id)
            return slot["preview"] if slot and self._alive(slot) else None

    def take(self, preview_id, mode):
        """승인 — 꺼내고 지운다. 없거나 지났거나 다른 모드의 것이면 None."""
        with self._lock:
            slot = self._items.get(preview_id)
            if not slot or slot["mode"] != mode:
                return None
            del self._items[preview_id]
            return slot["preview"] if self._alive(slot) else None


# ---------- 승인 이력 ----------
def _user_name():
    try:
        return getpass.getuser()
    except Exception:
        return ""


def approval_record(preview_id, preview):
    """승인 한 건 — 누가 언제 어디로 무엇(파일·요청 수·글자 수·SHA-256)을 보내기로 했는지."""
    dest = preview["destination"]
    sent = [item for item in preview["plan"] if item["requests"]]
    return {
        "event": "approved",
        "preview_id": preview_id,
        "user": _user_name(),
        "mode": preview["mode"],
        "customer": preview.get("customer") or "",
        "profile": preview.get("profile") or "",
        "run_id": preview.get("run_id") or "",
        "provider": dest["provider"],
        "host": dest["host"],
        "model": dest["model"],
        "keys": [key["name"] for key in dest.get("keys") or []],
        "files": [item["source"] for item in sent],
        "requests": sum(len(item["requests"].prompts) for item in sent),
        "chars": sum(len(prompt) for item in sent for prompt in item["requests"].prompts),
        "sha256": preview["sha256"],
    }


def finished_record(preview_id, *, sent, failed, keys_used, error=None, cancelled=False):
    """승인한 전송이 끝난 결과 — 실제로 응답을 받은 요청 수와 쓴 키(이름). cancelled: 작업 센터에서 취소해 남은
    요청을 보내지 않았다(승인한 것보다 덜 나갔다)."""
    record = {"event": "failed" if error else "cancelled" if cancelled else "finished", "preview_id": preview_id,
              "requests_sent": sent, "parts_failed": failed, "keys_used": list(keys_used)}
    if error:
        record["error"] = redact_secrets(str(error))[:300]
    return record


def append_record(record, path=None):
    """승인 이력(ai_transmissions.jsonl)에 한 줄을 더한다 — 보낸 내용은 넣지 않는다."""
    path = path or ai_transmission_log_path()
    line = json.dumps({"time": format_iso(), **record}, ensure_ascii=False)
    with _RECORD_LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return line
