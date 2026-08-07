"""
Report Export and Persistence Module.
Handles exporting report content to storage and creating archives/exports.
"""
from typing import Any
from core.storage_service import storage_service


class ReportExporter:
    """Exports and persists report files to the run's storage location."""

    @staticmethod
    def export_markdown_report(run_handle: Any, filename: str, content: str):
        """Save rendered markdown content to the reports directory of a run."""
        rel_path = f"reports/{filename}"
        return storage_service.save_text(run_handle, rel_path, content, overwrite=True)
