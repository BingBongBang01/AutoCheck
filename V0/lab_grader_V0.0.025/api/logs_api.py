"""LogsApiMixin — 아키텍처 탭 아래 '전체 로그' 탭: 최근 로그 조회 + 전체 로그 .txt 내보내기."""
import datetime
import shutil


class LogsApiMixin:
    def get_recent_logs(self, limit=300):
        from core.app_logger import get_recent_logs
        return get_recent_logs(limit)

    def export_full_log(self):
        """저장 다이얼로그로 세션 시작 이후의 전체 로그(화면에 표시되지 않는 부분 포함)를 .txt로 내보냄."""
        import webview
        from api.window_ref import get_window
        from core.app_logger import get_session_file_path

        window = get_window()
        default_name = f"autocheck_full_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=default_name,
            file_types=("Text files (*.txt)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".txt"):
            dst += ".txt"
        src = get_session_file_path()
        try:
            shutil.copyfile(src, dst)
        except FileNotFoundError:
            with open(dst, "w", encoding="utf-8") as f:
                f.write("")
        return {"path": dst}
