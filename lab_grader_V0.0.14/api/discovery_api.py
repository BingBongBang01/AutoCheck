"""DiscoveryApiMixin — .unl 파일 분석만 담당."""
import io
import contextlib
import webview
from api.window_ref import get_window


class DiscoveryApiMixin:
    def scan_network(self, target, port=22):
        from engine.network_discovery import scan
        return scan(target, int(port))
    def run_discovery(self):
        window = get_window()
        result = window.create_file_dialog(webview.OPEN_DIALOG, file_types=("EVE-NG lab (*.unl)", "All files (*.*)"))
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        from unl_parser import run_discovery, parse_unl
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_discovery(path)
        nodes, _ = parse_unl(path)
        return {"text": buf.getvalue(), "node_names": [n["name"] for n in nodes]}
