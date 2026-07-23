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
        try:
            window = get_window()
            result = window.create_file_dialog(webview.FileDialog.OPEN, file_types=("EVE-NG lab (*.unl)", "All files (*.*)"))
        except Exception as exc:
            return {"error": f"파일 선택 창을 열 수 없습니다: {exc}"}
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        from unl_parser import run_discovery, parse_unl
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                run_discovery(path)
            nodes, _ = parse_unl(path)
        except Exception as exc:
            return {"error": f".unl 파일을 분석하지 못했습니다: {exc}"}
        return {"text": buf.getvalue(), "node_names": [n["name"] for n in nodes]}
