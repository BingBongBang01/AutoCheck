"""
Report Data Aggregator Module.
Collects and aggregates raw, parsed, analysis, and health score data for report generation.
"""
from typing import Any, Dict, Optional
from core.storage_service import storage_service


class ReportDataAggregator:
    """Aggregates execution run data from storage service."""

    @staticmethod
    def aggregate_run_data(run_handle: Any) -> Dict[str, Any]:
        """Aggregate all relevant analysis, metadata, and summary data for a given run."""
        summary = storage_service.load_json(run_handle, "analysis/summary.json", default={})
        health = storage_service.load_json(run_handle, "analysis/health_score.json", default={})
        session = storage_service.load_session(run_handle)
        metadata = storage_service.load_run_metadata(run_handle)

        return {
            "run_id": getattr(run_handle, "run_id", "unknown"),
            "session": session,
            "metadata": metadata,
            "summary": summary,
            "health_score": health,
        }
