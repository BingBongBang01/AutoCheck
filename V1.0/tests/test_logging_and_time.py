"""로깅·시각 표기 — IMPROVEMENT_PLAN 3-8.

  1. 앱 버전: 화면은 번들 폴더를, run 기록은 앱 폴더만 봐서 EXE 의 session.json 에 'unknown' 이 남았다.
  2. core/storage_service 의 기록이 콘솔(stderr)로만 가서 콘솔 없는 EXE 에서는 사라졌다 -> 앱 로그로 잇는다.
  3. 세션 로그(app_full_*.log)가 보존 규칙 없이 실행마다 쌓였다.
  4. 실시간 경고의 시각이 'HH:MM:SS' 뿐이라 여러 날의 경고가 한 저장본에 섞이면 날짜를 알 수 없었다.
  5. 시각·버전 헬퍼(_now, _now_iso, _app_version)가 모듈마다 다시 정의돼 있었다.
"""
import ast
import datetime
import logging
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- 1. 버전

def test_app_version_prefers_the_bundle_folder(tmp_path, monkeypatch):
    from core import version
    from core.paths import AppPaths
    bundle, app = tmp_path / "bundle", tmp_path / "app"
    bundle.mkdir()
    app.mkdir()
    (app / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    monkeypatch.setattr(AppPaths, "bundle_root", classmethod(lambda cls: bundle))
    monkeypatch.setattr(AppPaths, "app_root", classmethod(lambda cls: app))
    version.app_version.cache_clear()
    try:
        assert version.app_version() == "0.0.1"                 # 번들에 없으면 앱 폴더
        version.app_version.cache_clear()
        (bundle / "VERSION").write_text("0.6.0\n", encoding="utf-8")
        assert version.app_version() == "0.6.0"                 # EXE: 번들 폴더(_MEIPASS)에 있다
        version.app_version.cache_clear()
        (bundle / "VERSION").unlink()
        (app / "VERSION").unlink()
        assert version.app_version() == "unknown"
    finally:
        version.app_version.cache_clear()


# --------------------------------------------------------------------------- 2. logging -> 앱 로그

@pytest.fixture
def bridge():
    from core import app_logger
    handler = app_logger.install_logging_bridge()
    yield app_logger
    logging.getLogger().removeHandler(handler)
    app_logger._logging_bridge = None


def _logged(app_logger, marker):
    lines = app_logger.get_recent_logs(limit=500, min_level="DEBUG")["lines"]
    return [line for line in lines if marker in line]


def test_storage_traces_reach_the_app_log_file_level_only(bridge):
    from core.storage_service import logger              # 모듈이 자기 logger 수준을 DEBUG 로 둔다
    logger.debug("save_json -> 표식-3-8-a")
    lines = _logged(bridge, "표식-3-8-a")
    assert lines and "[DEBUG] [storage_service]" in lines[0]
    shown = bridge.get_recent_logs(limit=500)["lines"]          # 화면 기본(INFO 이상)에는 안 보인다
    assert not [line for line in shown if "표식-3-8-a" in line]


def test_library_chatter_below_warning_stays_out(bridge):
    logging.getLogger("paramiko.transport").info("표식-3-8-b 잡담")
    logging.getLogger("paramiko.transport").warning("표식-3-8-c 경고")
    assert not _logged(bridge, "표식-3-8-b")
    assert _logged(bridge, "표식-3-8-c")


def test_the_bridge_is_installed_once(bridge):
    assert bridge.install_logging_bridge() is bridge.install_logging_bridge()
    count = sum(isinstance(h, bridge._AppLogHandler) for h in logging.getLogger().handlers)
    assert count == 1


# --------------------------------------------------------------------------- 3. 로그 보존

def test_old_session_logs_are_pruned_but_recent_ones_kept(tmp_path, monkeypatch):
    from core import app_logger
    from core.paths import AppPaths
    monkeypatch.setattr(AppPaths, "logs_root", classmethod(lambda cls: tmp_path))
    now = time.time()
    day = 86400
    for index in range(25):                                     # 0 = 가장 최근
        path = tmp_path / f"app_full_2026{index:04d}.log"
        path.write_text("x", encoding="utf-8")
        age = index * 3 * day                                   # 0, 3, 6, ... 72일 전
        os.utime(path, (now - age, now - age))
    current = tmp_path / "app_full_20260000.log"
    monkeypatch.setattr(app_logger, "_session_file_path", str(current))
    other = tmp_path / "notes.txt"
    other.write_text("x", encoding="utf-8")
    os.utime(other, (now - 400 * day, now - 400 * day))

    removed = app_logger.prune_old_logs(max_age_days=30, keep_latest=20, now=now)
    left = sorted(p.name for p in tmp_path.glob("app_full_*.log"))
    assert removed == 5 and len(left) == 20                     # 최근 20개는 30일이 넘었어도 남는다
    assert current.exists() and other.exists()                  # 지금 쓰는 파일·다른 파일은 건드리지 않는다


# --------------------------------------------------------------------------- 4. 경고의 날짜 포함 시각

def _alert(alert_id, at=None):
    alert = {"alert_id": alert_id, "device": "Core1", "type": "VLAN_REMOVED", "severity": "CRITICAL",
             "message": "vlan 100 삭제", "ts": "23:59:01", "target": f"vlan:{alert_id}"}
    if at:
        alert["at"] = at
    return alert


def test_new_alerts_carry_a_dated_timestamp():
    from engine.realtime_monitor import RealtimeMonitor
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.apply_alerts([_alert("a1")])
    at = monitor.alerts("Core1")[0]["at"]
    assert datetime.datetime.fromisoformat(at).date() == datetime.date.today()


def test_restored_alerts_get_their_own_day_not_the_save_day():
    from engine.realtime_monitor import RealtimeMonitor
    today = datetime.date.today()
    two_days_ago = (today - datetime.timedelta(days=2)).isoformat() + "T23:59:01"
    yesterday = (today - datetime.timedelta(days=1)).isoformat() + "T23:59:01"
    snapshot = {"saved_at": time.time() - 86400, "devices": ["Core1"],
                "alerts": [_alert("old", at=two_days_ago), _alert("mid", at=yesterday), _alert("legacy")]}
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.restore(snapshot)
    ts = {a["alert_id"]: a["ts"] for a in monitor.alerts("Core1", 10)}
    fmt = lambda d: d.strftime("%m-%d ")
    assert ts["old"] == fmt(today - datetime.timedelta(days=2)) + "23:59:01"
    assert ts["mid"] == fmt(today - datetime.timedelta(days=1)) + "23:59:01"
    assert ts["legacy"] == fmt(today - datetime.timedelta(days=1)) + "23:59:01"   # 예전 저장본: 저장한 날


# --------------------------------------------------------------------------- 5. 헬퍼 중복 금지

def test_time_and_version_helpers_are_not_redefined():
    allowed = {"core/utils/datetime.py", "core/version.py"}
    offenders = []
    for layer in ("api", "engine", "core", "report"):
        for path in sorted((ROOT / layer).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.FunctionDef) and node.name in ("_now", "_now_iso", "_app_version"):
                    offenders.append(f"{rel}:{node.lineno} {node.name}")
    assert offenders == [], f"core.utils.format_iso / core.version.app_version 를 쓴다: {offenders}"
