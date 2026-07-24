"""app_ingestion: Log_Inspection (JSONB + monthly partitioning).

The table is created via raw SQL (webapp/backend/sql/001_partitioning.sql)
because native PostgreSQL declarative partitioning is not expressible through
the Django ORM's CREATE TABLE. The model below is `managed = False` and maps
onto that table for ORM read/write access.
"""
from __future__ import annotations

import uuid

from django.db import models

from app_core.models import Device, HealthStatus


class Inspection(models.Model):
    """Log_Inspection (parent of the partitioned table)."""

    inspection_id: models.UUIDField = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    device: models.ForeignKey = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="inspections",
        db_column="device_id", to_field="device_id",
    )
    inspection_date: models.DateField = models.DateField(db_index=True)
    inspector_name: models.CharField = models.CharField(max_length=255)

    raw_metrics: models.JSONField = models.JSONField(default=dict)
    health_status: models.CharField = models.CharField(
        max_length=16, choices=HealthStatus.choices, default=HealthStatus.NORMAL,
    )

    class Meta:
        managed = False
        db_table = "log_inspection"
