"""Device Inventory — 도달가능성 체크(Dashboard의 Reachable/Offline용).
socket 레벨 포트 체크만 수행 — SSH 인증은 하지 않음(가벼움)."""
import socket as _socket

from engine.device_inventory_core import resolve_credentials


def _check_one(ip, port, timeout):
    if not ip:
        return False
    try:
        with _socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_reachability(devices, defaults, timeout=2):
    """socket 레벨 포트 체크만 — SSH 인증은 안 함(가벼움). {"name": bool} 반환."""
    result = {}
    for d in devices:
        ip, port, _, _ = resolve_credentials(d, defaults)
        result[d["name"]] = _check_one(ip, port, timeout)
    return result
