"""app_core: shared domain models (Core_Device) and base mixins.

Standalone subproject (Plan C: Modular Monolith with Dynamic Schema Factory).
Not wired into the existing pywebview app in this repo.
"""
from __future__ import annotations

import uuid

from django.db import models


class Location(models.Model):
    location_id: models.UUIDField = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    name: models.CharField = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = "core_location"

    def __str__(self) -> str:
        return self.name


class Device(models.Model):
    """Core_Device"""

    device_id: models.UUIDField = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    hostname: models.CharField = models.CharField(max_length=255, unique=True, db_index=True)
    model: models.CharField = models.CharField(max_length=128, db_index=True)
    serial_number: models.CharField = models.CharField(max_length=128, unique=True)
    location: models.ForeignKey = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="devices",
        db_column="location_id", to_field="location_id",
    )

    class Meta:
        db_table = "core_device"

    def __str__(self) -> str:
        return self.hostname


class HealthStatus(models.TextChoices):
    NORMAL = "NORMAL", "Normal"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"
    FAILED = "FAILED", "Failed"
