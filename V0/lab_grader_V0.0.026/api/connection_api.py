"""ConnectionApiMixin — SSH 접속 옵션(Port/Timeout/Retry/Thread)만 관리. IP는 여기서 안 다룸.
장비별 인증(비밀번호/키)은 Device Inventory에서 관리하고, 실제 터미널 접속은 TerminalApiMixin이 담당.

고객사·프로파일과 무관한 앱 전역 설정이다(core/app_settings.py 참고)."""
import os

from core.app_settings import connection_path
from core.atomic_io import dump_yaml_atomic


class ConnectionApiMixin:
    def get_connection_settings(self):
        import yaml
        if not os.path.exists(connection_path()):
            return {}
        with open(connection_path(), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        net = cfg.get("network", {})
        ssh = cfg.get("ssh", {})
        thread = cfg.get("thread", {})
        return {
            "check_target_node": net.get("check_target_node", "Core1"),
            "retry_count": ssh.get("retry_count", 1),
            "retry_delay_sec": ssh.get("retry_delay_sec", 5),
            "ssh_timeout": ssh.get("connect_timeout_sec", 20),
            "max_parallel_workers": thread.get("max_parallel_workers", ""),
        }

    def save_connection_settings(self, payload):
        import yaml
        cfg = {
            "network": {
                "mode": "internal", "pre_flight_check": True,
                "check_target_node": payload.get("check_target_node", "Core1"),
                "check_port": 22, "check_timeout_sec": 3,
            },
            "ssh": {
                "connect_timeout_sec": int(payload.get("ssh_timeout", 20)),
                "retry_count": int(payload.get("retry_count", 1)),
                "retry_delay_sec": int(payload.get("retry_delay_sec", 5)),
            },
            "thread": {
                "max_parallel_workers": int(payload["max_parallel_workers"]) if payload.get("max_parallel_workers") else None,
            },
        }
        dump_yaml_atomic(cfg, connection_path())
        return True
