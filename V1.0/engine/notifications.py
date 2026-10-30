"""알림 채널 — '사람이 지금 봐야 할 일'을 Teams·Slack·웹훅·메일로 보낸다(IMPROVEMENT_PLAN 4-4).

예전 알람(alarm/·AlarmStep)은 채점 파이프라인과 함께 3-1 에서 퇴역했다 — 그때도 FAIL 은 High 까지만 매겨지는데
Critical 만 보내서 사실상 울리지 않았고, 핸들러는 콘솔·파일뿐이었다. 새로 만든다.

보내는 사건(사건마다 켜고 끈다):
  - realtime          : 실시간 감시 경고(기본 CRITICAL 만, MAJOR 까지 넓힐 수 있다). 감시 스레드를 막지 않게
                        백그라운드에서 보낸다.
  - scheduled_failed  : 무인 정기점검 실패(사전점검 실패·전체 수집 실패·보고서 실패 등 — 4-3)
  - collection_failed : 무인 정기점검 중 일부 장비를 수집하지 못함
  - test              : 설정 화면의 '시험 보내기'(중복 억제·한도와 무관하게 바로 보낸다)

설정은 <사용자 데이터>/config/alarm.yaml. 웹훅 주소(경로가 곧 비밀 토큰이다)와 SMTP 비밀번호는 DPAPI 로 봉인해
두고(4-1) 화면에는 저장 여부만 보낸다. 내용은 보내기 전에 가린다 — 기본은 비밀값·IP·MAC·계정이고 장비명은
남긴다(받는 쪽이 어느 장비인지 알아야 움직일 수 있다). 같은 사건은 dedupe_minutes 안에 한 번만, 채널마다 한
시간에 max_per_hour 건까지만 보낸다(넘친 건수는 다음 메시지에 적는다). 실패는 앱 로그와 채널 상태(화면)에
남기고 부른 쪽으로 던지지 않는다 — 알림이 안 간다고 점검이 멈추면 안 된다. 웹훅 주소는 로그에 호스트만 남긴다.
"""
import atexit
import copy
import json
import os
import queue
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from email.message import EmailMessage
from urllib.parse import urlparse

import yaml

from core.app_settings import alarm_config_path
from core.atomic_io import dump_yaml_atomic
from core.cloud_providers import is_loopback_host
from core.secret_redaction import redact_secrets
from core.secret_vault import VaultError, reveal, seal
from core.utils.datetime import format_iso
from engine.log_masking import SECRET_KEYS as MASK_SECRET_KEYS
from engine.log_masking import apply_masking, mask_for_external_ai

CHANNEL_TYPES = {"teams": "Microsoft Teams", "slack": "Slack", "webhook": "웹훅(사내 메신저)", "email": "이메일(SMTP)"}
EVENT_LABELS = {"realtime": "실시간 감시 경고", "scheduled_failed": "무인 정기점검 실패",
                "collection_failed": "무인 점검 수집 실패", "test": "시험"}
MASK_LEVELS = {"secrets": "비밀값·IP·MAC·계정", "all": "장비명까지", "none": "가리지 않음"}
SECURITY_PORTS = {"starttls": 587, "ssl": 465, "none": 25}
SEVERITY_RANK = {"WARNING": 1, "MAJOR": 2, "CRITICAL": 3}
SECRET_FIELDS = ("webhook_url", "password")
DEFAULTS = {
    "enabled": True, "mask": "secrets", "dedupe_minutes": 10, "max_per_hour": 30,
    "events": {"realtime": True, "realtime_min_severity": "CRITICAL",
               "scheduled_failed": True, "collection_failed": True},
    "channels": [],
}
SEND_TIMEOUT_SEC = 15
MAX_LINES = 10
_SECRETS_ONLY = frozenset(MASK_SECRET_KEYS) | {"ip_address", "mac_address", "account_name"}
_EMAIL_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


class NotificationError(ValueError):
    """화면에 그대로 보여 줄 수 있는 알림 설정 오류."""


# ---------- 설정 ----------
def load_settings():
    """설정 — 비밀값을 푼 사본. 이 PC 에서 풀 수 없는 값은 빈 값으로 두고 원래 봉인 문자열을 _sealed 에 기억한다
    (화면은 '다시 입력'으로 안내하고, 저장할 때 원래 값을 되돌려 쓴다)."""
    raw = {}
    path = alarm_config_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as stream:
                raw = yaml.safe_load(stream) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"[알림] 설정 파일을 읽을 수 없습니다({exc}) — 기본값으로 봅니다.")
    raw = raw if isinstance(raw, dict) else {}
    settings = copy.deepcopy(DEFAULTS)
    settings.update({k: v for k, v in raw.items() if k in DEFAULTS and k not in ("events", "channels")})
    settings["events"].update({k: v for k, v in (raw.get("events") or {}).items() if k in DEFAULTS["events"]})
    settings["channels"] = []
    for channel in raw.get("channels") or []:
        if not isinstance(channel, dict) or not channel.get("id"):
            continue
        channel = dict(channel)
        for field in SECRET_FIELDS:
            value = channel.get(field) or ""
            try:
                channel[field] = reveal(value)
            except VaultError:
                channel.setdefault("_sealed", {})[field] = value
                channel[field] = ""
        settings["channels"].append(channel)
    return settings


def save_settings(settings):
    """봉인한 사본을 쓴다(메모리의 settings 는 그대로)."""
    data = copy.deepcopy(settings)
    for channel in data.get("channels") or []:
        kept = channel.pop("_sealed", {}) or {}
        for field in SECRET_FIELDS:
            value = channel.get(field) or ""
            channel[field] = seal(value) if value else kept.get(field, "")
    dump_yaml_atomic(data, alarm_config_path())


def _host_of(url):
    return urlparse(url or "").hostname or ""


def public_settings(settings=None):
    """화면에 보낼 설정 — 웹훅 주소·비밀번호 대신 저장 여부(has_*)와 웹훅 호스트만."""
    settings = settings or load_settings()
    channels = []
    for channel in settings["channels"]:
        public = {k: v for k, v in channel.items() if k not in SECRET_FIELDS and not k.startswith("_")}
        public.update(has_webhook_url=bool(channel.get("webhook_url")), webhook_host=_host_of(channel.get("webhook_url")),
                      has_password=bool(channel.get("password")),
                      unreadable_secrets=sorted((channel.get("_sealed") or {}).keys()),
                      status=notifier.status(channel["id"]))
        channels.append(public)
    return {**{key: settings[key] for key in ("enabled", "mask", "dedupe_minutes", "max_per_hour")},
            "events": dict(settings["events"]), "channels": channels,
            "channel_types": CHANNEL_TYPES, "mask_levels": MASK_LEVELS, "event_labels": EVENT_LABELS}


def _int(value, low, high, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise NotificationError(f"{label} 값이 올바르지 않습니다: {value}") from None
    if not low <= number <= high:
        raise NotificationError(f"{label}은(는) {low}~{high} 사이로 넣으세요.")
    return number


def apply_options(settings, fields):
    """전체 옵션(사용·가림 수준·중복 억제·한도·사건) 반영. 채널은 건드리지 않는다."""
    fields = fields or {}
    events = dict(settings["events"])
    for key, value in (fields.get("events") or {}).items():
        if key == "realtime_min_severity":
            if value not in ("CRITICAL", "MAJOR"):
                raise NotificationError("실시간 경고의 최소 심각도는 CRITICAL 또는 MAJOR 입니다.")
            events[key] = value
        elif key in DEFAULTS["events"]:
            events[key] = bool(value)
    mask = fields.get("mask", settings["mask"])
    if mask not in MASK_LEVELS:
        raise NotificationError(f"가림 수준이 올바르지 않습니다: {mask}")
    settings.update(enabled=bool(fields.get("enabled", settings["enabled"])), mask=mask, events=events,
                    dedupe_minutes=_int(fields.get("dedupe_minutes", settings["dedupe_minutes"]), 0, 1440,
                                        "중복 억제 시간(분)"),
                    max_per_hour=_int(fields.get("max_per_hour", settings["max_per_hour"]), 1, 1000,
                                      "시간당 최대 건수"))
    return settings


def _webhook_problem(url):
    parsed = urlparse((url or "").strip())
    if parsed.scheme == "https" and parsed.hostname:
        return None
    if parsed.scheme == "http" and is_loopback_host(parsed.hostname):
        return None
    return ("웹훅 주소는 https:// 로 시작해야 합니다(같은 PC 의 http://localhost 만 예외) — 점검 결과가 암호화되지 "
            "않은 채 오가면 안 됩니다.")


def _emails(value, label):
    items = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value or ""))
    items = [item.strip() for item in items if item and item.strip()]
    bad = [item for item in items if not _EMAIL_RE.fullmatch(item)]
    if bad:
        raise NotificationError(f"{label} 메일 주소가 올바르지 않습니다: {', '.join(bad)}")
    return items


def apply_channel(settings, fields):
    """채널 하나를 추가하거나 고친다 -> 그 채널. 비밀값(웹훅 주소·비밀번호)은 새로 넣은 것만 바꾸고(빈 값 = 그대로),
    clear_secrets 에 적은 칸은 지운다 — 장비 목록의 비밀 칸(4-1)과 같은 규칙."""
    fields = fields or {}
    previous = next((c for c in settings["channels"] if c["id"] == fields.get("id")), None) if fields.get("id") else None
    kind = fields.get("type") or (previous or {}).get("type")
    if kind not in CHANNEL_TYPES:
        raise NotificationError(f"알림 채널 종류를 고르세요({', '.join(CHANNEL_TYPES.values())}).")
    channel = dict(previous or {"id": uuid.uuid4().hex[:12]})
    if previous and previous.get("type") != kind:
        channel.pop("_sealed", None)                    # 종류가 바뀌면 예전 비밀값은 뜻이 없다
        for field in SECRET_FIELDS:
            channel[field] = ""
    channel["type"] = kind
    channel["name"] = (str(fields.get("name") or "").strip() or channel.get("name") or CHANNEL_TYPES[kind])[:60]
    channel["enabled"] = bool(fields.get("enabled", channel.get("enabled", True)))
    for field in SECRET_FIELDS:
        if field in (fields.get("clear_secrets") or []):
            channel[field] = ""
            (channel.get("_sealed") or {}).pop(field, None)
        elif str(fields.get(field) or "").strip():
            channel[field] = str(fields[field]).strip()
            (channel.get("_sealed") or {}).pop(field, None)
    if kind == "email":
        security = fields.get("security") or channel.get("security") or "starttls"
        if security not in SECURITY_PORTS:
            raise NotificationError("메일 보안 방식은 STARTTLS·SSL·없음 중에서 고르세요.")
        host = str(fields.get("smtp_host", channel.get("smtp_host")) or "").strip()
        if not host:
            raise NotificationError("SMTP 서버 주소를 넣으세요.")
        port = _int(fields.get("smtp_port") or channel.get("smtp_port") or SECURITY_PORTS[security], 1, 65535,
                    "SMTP 포트")
        if security == "none" and (channel.get("password") or (channel.get("_sealed") or {}).get("password")):
            raise NotificationError("비밀번호를 암호화되지 않은 연결로 보낼 수 없습니다 — 보안 방식을 STARTTLS 나 SSL 로 "
                                    "고르거나 비밀번호를 지우세요.")
        sender = _emails(fields.get("sender", channel.get("sender")), "보내는 사람")
        recipients = _emails(fields.get("recipients", channel.get("recipients")), "받는 사람")
        if len(sender) != 1:
            raise NotificationError("보내는 사람 메일 주소를 하나 넣으세요.")
        if not recipients:
            raise NotificationError("받는 사람 메일 주소를 하나 이상 넣으세요.")
        channel.update(smtp_host=host, smtp_port=port, security=security, sender=sender[0], recipients=recipients,
                       username=str(fields.get("username", channel.get("username")) or "").strip())
        channel["webhook_url"] = ""
    else:
        if not channel.get("webhook_url") and not (channel.get("_sealed") or {}).get("webhook_url"):
            raise NotificationError("웹훅 주소를 넣으세요.")
        if channel.get("webhook_url"):
            problem = _webhook_problem(channel["webhook_url"])
            if problem:
                raise NotificationError(problem)
        channel["password"] = ""
        for key in ("smtp_host", "smtp_port", "security", "sender", "recipients", "username"):
            channel.pop(key, None)
    if previous is None:
        settings["channels"].append(channel)
    else:
        settings["channels"][settings["channels"].index(previous)] = channel
    return channel


def remove_channel(settings, channel_id):
    before = len(settings["channels"])
    settings["channels"] = [c for c in settings["channels"] if c["id"] != channel_id]
    return len(settings["channels"]) != before


# ---------- 메시지 ----------
def _mask(text, level, names=()):
    if level == "none":
        return text
    if level == "all":
        return mask_for_external_ai(text, names)
    return apply_masking(text, _SECRETS_ONLY)


def _clip(text, limit=200):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def realtime_event(alerts, customer="", profile=""):
    """실시간 경고 묶음 -> 사건 하나(메시지 한 통). 심각도가 높은 것부터 MAX_LINES 건."""
    ordered = sorted(alerts, key=lambda a: -SEVERITY_RANK.get(str(a.get("severity")).upper(), 0))
    lines = [f"[{a.get('severity')}] {a.get('device') or '-'} · {_clip(a.get('message'), 120)}"
             + (f" — {_clip(a.get('raw_line'))}" if a.get("raw_line") else "") for a in ordered[:MAX_LINES]]
    if len(ordered) > MAX_LINES:
        lines.append(f"외 {len(ordered) - MAX_LINES}건")
    where = " / ".join(part for part in (customer, profile) if part)
    top = str(ordered[0].get("severity")).upper() if ordered else "WARNING"
    return {"kind": "realtime", "severity": top, "customer": customer, "profile": profile,
            "title": f"실시간 감시 경고 {len(ordered)}건" + (f" — {where}" if where else ""),
            "lines": lines, "devices": sorted({a.get("device") for a in ordered if a.get("device")})}


def scheduled_event(record):
    """무인 정기점검 기록 -> 사건(실패면 scheduled_failed, 장비 수집 실패가 있는 일부 실패면 collection_failed).
    알릴 것이 없으면(성공·건너뜀) None."""
    status = record.get("status")
    failed_devices = record.get("devices_failed") or {}
    if status == "failed" or (status == "partial" and not failed_devices):
        kind, severity = "scheduled_failed", "CRITICAL" if status == "failed" else "MAJOR"
    elif status == "partial":
        kind, severity = "collection_failed", "MAJOR"
    else:
        return None
    label = {"failed": "실패", "partial": "일부 실패"}[status]
    where = " / ".join(part for part in (record.get("customer"), record.get("profile")) if part) or "-"
    lines = [record.get("message") or "",
             f"시작 {str(record.get('started_at') or '').replace('T', ' ')} · "
             f"{'지금 실행' if record.get('trigger') == 'manual' else '예약 실행'}"]
    lines += [f"수집 실패: {name} — {_clip(error, 120)}" for name, error in list(failed_devices.items())[:MAX_LINES]]
    if len(failed_devices) > MAX_LINES:
        lines.append(f"외 {len(failed_devices) - MAX_LINES}대")
    lines += [f"보고서 실패({fmt}): {_clip(error, 120)}" for fmt, error in (record.get("report_errors") or {}).items()]
    return {"kind": kind, "severity": severity, "customer": record.get("customer") or "",
            "profile": record.get("profile") or "", "title": f"무인 정기점검 {label} — {where}",
            "lines": [line for line in lines if line], "devices": sorted(failed_devices) + sorted(record.get("devices_ok") or []),
            "key": f"{kind}:{record.get('schedule_id')}:{record.get('message')}"}


def render(event, settings, dropped=0):
    """사건 -> {"title", "text", …} — 보내기 전에 가린다."""
    lines = list(event["lines"])
    if dropped:
        lines.append(f"(시간당 한도 때문에 앞서 보내지 못한 알림 {dropped}건이 있습니다)")
    lines.append(f"— AutoCheck · {format_iso().replace('T', ' ')}")
    text = _mask("\n".join(lines), settings["mask"], event.get("devices") or ())
    return {"title": f"[AutoCheck] {event['title']}", "text": text, "kind": event["kind"],
            "severity": event.get("severity", ""), "customer": event.get("customer", ""),
            "profile": event.get("profile", "")}


def _slack_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def payload(channel_type, message):
    """채널 종류별 요청 본문. Teams 는 Workflows·예전 커넥터가 모두 받는 Adaptive Card 봉투."""
    if channel_type == "slack":
        return {"text": f"*{_slack_escape(message['title'])}*\n{_slack_escape(message['text'])}"}
    if channel_type == "teams":
        body = [{"type": "TextBlock", "text": message["title"], "weight": "Bolder", "size": "Medium", "wrap": True,
                 "color": "Attention" if message["severity"] in ("CRITICAL", "MAJOR") else "Default"}]
        body += [{"type": "TextBlock", "text": line, "wrap": True, "spacing": "None"}
                 for line in message["text"].splitlines() if line.strip()]
        return {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": body}}]}
    return {"source": "AutoCheck", "kind": message["kind"], "severity": message["severity"],
            "title": message["title"], "text": message["text"], "customer": message["customer"],
            "profile": message["profile"], "time": format_iso()}


def _post_webhook(channel, message):
    data = json.dumps(payload(channel["type"], message), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(channel["webhook_url"], data=data, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "User-Agent": "AutoCheck"})
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SEC) as response:
        response.read()


def _send_email(channel, message):
    mail = EmailMessage()
    mail["Subject"] = message["title"]
    mail["From"] = channel["sender"]
    mail["To"] = ", ".join(channel["recipients"])
    mail.set_content(message["text"])
    context = ssl.create_default_context()
    if channel.get("security") == "ssl":
        server = smtplib.SMTP_SSL(channel["smtp_host"], channel["smtp_port"], timeout=SEND_TIMEOUT_SEC, context=context)
    else:
        server = smtplib.SMTP(channel["smtp_host"], channel["smtp_port"], timeout=SEND_TIMEOUT_SEC)
    with server:
        if channel.get("security") == "starttls":
            server.starttls(context=context)
        if channel.get("username") and channel.get("password"):
            server.login(channel["username"], channel["password"])
        server.send_message(mail)


def deliver(channel, message):
    """채널 하나로 보낸다 -> None(성공) | 오류 문구. 예외를 던지지 않고, 오류 문구에 웹훅 주소를 넣지 않는다."""
    try:
        if channel["type"] == "email":
            _send_email(channel, message)
        else:
            _post_webhook(channel, message)
        return None
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        text = str(exc)
        if channel.get("webhook_url"):
            text = text.replace(channel["webhook_url"], _host_of(channel["webhook_url"]))
        if channel.get("password"):
            text = text.replace(channel["password"], "[REDACTED]")
        return redact_secrets(f"{type(exc).__name__}: {text}")[:300]


# ---------- 보내기(중복 억제·한도·백그라운드) ----------
class Notifier:
    """사건을 채널들로 보낸다. 같은 사건은 dedupe 창 안에 한 번만, 채널마다 한 시간 한도. 상태는 이 프로세스 안에만
    둔다 — 작업 스케줄러가 띄운 무인 점검은 따로 센다(드물다)."""

    def __init__(self, clock=time.time, send=None):
        self._clock = clock
        self._send = send or deliver
        self._lock = threading.Lock()
        self._seen = {}                  # 사건 키 -> 마지막으로 보낸 시각
        self._windows = {}               # 채널 id -> 최근 한 시간 보낸 시각들
        self._dropped = {}               # 채널 id -> 한도로 못 보낸 건수(다음 메시지에 적는다)
        self._status = {}                # 채널 id -> {ok, at, error}
        self._queue = None
        self._worker = None

    def status(self, channel_id):
        with self._lock:
            return dict(self._status.get(channel_id) or {})

    def _fresh(self, key, window_sec, now):
        """처음 보는 사건이면 기록하고 True — dedupe 창 안에 같은 키를 이미 보냈으면 False."""
        if not key or window_sec <= 0:
            return True
        with self._lock:
            last = self._seen.get(key)
            if last is not None and now - last < window_sec:
                return False
            self._seen[key] = now
            return True

    def _take_slot(self, channel_id, limit, now):
        """한도 안이면 (True, 앞서 못 보낸 건수) — 넘었으면 (False, 0) 하고 못 보낸 건수를 센다."""
        with self._lock:
            window = self._windows.setdefault(channel_id, deque())
            while window and now - window[0] >= 3600:
                window.popleft()
            if len(window) >= limit:
                self._dropped[channel_id] = self._dropped.get(channel_id, 0) + 1
                return False, 0
            window.append(now)
            return True, self._dropped.pop(channel_id, 0)

    def dispatch(self, event, settings=None, *, force_channel=None):
        """사건 하나를 켜진 채널들로 바로 보낸다 -> {채널 id: None|오류}. force_channel: 시험(한도·중복·사건 설정 무시)."""
        settings = settings or load_settings()
        now = self._clock()
        if force_channel is None:
            if not settings["enabled"] or not settings["events"].get(event["kind"]):
                return {}
            if not self._fresh(event.get("key"), settings["dedupe_minutes"] * 60, now):
                return {}
            channels = [c for c in settings["channels"] if c.get("enabled")]
        else:
            channels = [force_channel]
        results = {}
        for channel in channels:
            dropped = 0
            if force_channel is None:
                allowed, dropped = self._take_slot(channel["id"], settings["max_per_hour"], now)
                if not allowed:
                    continue
            error = self._send(channel, render(event, settings, dropped))
            results[channel["id"]] = error
            with self._lock:
                self._status[channel["id"]] = {"ok": error is None, "at": format_iso(), "error": error,
                                               "kind": event["kind"]}
            if error:
                print(f"[알림] '{channel.get('name')}'({CHANNEL_TYPES.get(channel['type'])}) 보내기 실패: {error}")
        return results

    def notify_realtime(self, alerts, customer="", profile=""):
        """실시간 감시 경고 — 기준 심각도 이상, 처음 보는 것만 골라 백그라운드로 보낸다(감시 스레드를 막지 않는다)."""
        settings = load_settings()
        if not settings["enabled"] or not settings["events"]["realtime"] or \
                not any(c.get("enabled") for c in settings["channels"]):
            return 0
        floor = SEVERITY_RANK[settings["events"]["realtime_min_severity"]]
        now = self._clock()
        picked = [a for a in alerts if SEVERITY_RANK.get(str(a.get("severity")).upper(), 0) >= floor
                  and not a.get("history")
                  and self._fresh(f"realtime:{a.get('device')}:{a.get('rule_id') or a.get('type')}:"
                                  f"{_clip(a.get('message'), 80)}", settings["dedupe_minutes"] * 60, now)]
        if picked:
            self._enqueue(lambda: self.dispatch(realtime_event(picked, customer, profile), settings))
        return len(picked)

    def notify_scheduled(self, record):
        """무인 정기점검 결과 — 그 자리에서 보낸다(작업 스케줄러가 띄운 프로세스는 곧 끝난다)."""
        event = scheduled_event(record)
        return self.dispatch(event) if event else {}

    def test(self, channel):
        """'시험 보내기' — 한도·중복·사건 설정과 무관하게 이 채널로 바로 보낸다 -> None|오류."""
        event = {"kind": "test", "severity": "WARNING", "customer": "", "profile": "",
                 "title": "알림 시험", "lines": ["AutoCheck 알림 채널이 연결되었습니다."], "devices": []}
        return self.dispatch(event, force_channel=channel).get(channel["id"])

    def _enqueue(self, job):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._queue = queue.Queue()
                self._worker = threading.Thread(target=self._run, name="notifications", daemon=True)
                self._worker.start()
            self._queue.put(job)

    def _run(self):
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception as exc:      # 알림 하나가 실패해도 다음 알림은 보낸다
                print(f"[알림] 보내기 중 오류: {exc}")
            finally:
                self._queue.task_done()

    def flush(self, timeout=10.0):
        """밀린 알림이 다 나갈 때까지(최대 timeout 초) 기다린다 — 테스트와 앱 종료 때."""
        pending = self._queue
        if pending is None:
            return True
        deadline = time.time() + timeout
        while pending.unfinished_tasks and time.time() < deadline:
            time.sleep(0.02)
        return not pending.unfinished_tasks


notifier = Notifier()


def _flush_at_exit():
    """앱을 닫을 때 밀린 실시간 알림을 잠깐(최대 5초) 기다려 보낸다 — 보내는 스레드는 daemon 이라 그냥 끝나면 버려진다."""
    try:
        notifier.flush(timeout=5.0)
    except Exception:
        pass


atexit.register(_flush_at_exit)
