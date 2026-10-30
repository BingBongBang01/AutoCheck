"""
LogsApiMixin — 아키텍처 탭 아래 '전체 로그' 탭:
1. 최근/Delta 로그 폴링 (get_recent_logs — since_index 및 배치/속도제어 상한 지원)
2. 전체 로그 .txt 내보내기 (export_full_log)
3. 진단 번들 내보내기 (export_diagnostics_bundle — engine/diagnostics.py, IMPROVEMENT_PLAN 4-10)
"""
import datetime
import shutil
from typing import Any, Dict, Optional, Union


class LogsApiMixin:
    def get_recent_logs(
        self,
        limit: int = 300,
        since_index: Optional[int] = None,
        min_level: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        UI 표시용 최근 로그 또는 Delta 로그 반환.
        - since_index: 이전 폴링 시 수신한 total_count 인덱스. 지정 시 델타(새 로그만) 전달.
        - limit: 1회 수신 최대 라인 수 (최대 500줄 IPC 속도제어 제한)
        """
        from core.app_logger import get_recent_logs as _get_recent_logs
        return _get_recent_logs(limit=limit, since_index=since_index, min_level=min_level)

    def export_full_log(self):
        """저장 다이얼로그로 세션 시작 이후의 전체 로그(화면에 표시되지 않는 부분 포함)를 .txt로 내보냄."""
        import webview
        from api.window_ref import get_window
        from core.app_logger import get_session_file_path, flush_logs

        flush_logs()  # 내보내기 전 대기 중인 로그 디스크 동기화
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

    def export_diagnostics_bundle(self, mask_identifiers=True):
        """'진단 번들' — 앱 정보·최근 로그·설정을 zip 하나로 저장 다이얼로그에 내보낸다. 비밀(비밀번호·키·토큰)은
        언제나 지우고, mask_identifiers 이면 장비·고객사 이름과 IP 등도 가린다(기본 — 번들은 이 PC 밖으로 나간다)."""
        import webview
        from api.window_ref import get_window
        from core.app_logger import flush_logs
        from engine import diagnostics

        flush_logs()
        window = get_window()
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=diagnostics.default_name(), file_types=("Zip files (*.zip)",))
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".zip"):
            dst += ".zip"
        try:
            return {"ok": True, **diagnostics.build_bundle(dst, mask_identifiers=bool(mask_identifiers))}
        except OSError as exc:
            return {"error": f"진단 번들을 저장하지 못했습니다: {exc}"}
