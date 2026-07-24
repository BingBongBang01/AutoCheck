"""app_parser.schema_factory

Dynamic Schema Factory: selects a per-customer/device-profile parser strategy
and normalizes each into the (health_status, raw_metrics) shape stored on
Log_Inspection.

Known accepted quirk: Arista `secret sha512 $6$...` / `password 7 ...` lines
are frequently truncated mid-hash by upstream log capture tooling. That is a
capture artifact, not a device fault, so it must never surface as a parse
error or lower the inspection's health_status. We detect the pattern and
store the raw fragment for audit traceability instead of trying to validate
or reassemble it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

HASH_FRAGMENT_PATTERN = re.compile(
    r"(secret\s+sha512\s+\$6\$|password\s+7\s+)[A-Za-z0-9./$]+"
)

CPU_MEM_PATTERN = re.compile(r"(CPU|Memory)\D{0,10}(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
INTERFACE_ERROR_PATTERN = re.compile(
    r"(?P<iface>Et\d+/\d+)\D+?(?P<err_type>FCS|CRC|Runts|Giants)\D+?(?P<count>\d+)"
)

RESOURCE_WARNING_THRESHOLD = 70.0


@dataclass
class ParseResult:
    health_status: str
    raw_metrics: dict = field(default_factory=dict)
    strict_validation: bool = True


class DeviceLogParser(Protocol):
    def parse(self, raw_text: str) -> ParseResult: ...


def extract_fragmented_hashes(raw_text: str) -> list[str]:
    """Find password/secret hash lines truncated by log capture.

    Returns the fragments as-is (never reassembled) so callers can stash them
    under `raw_fragmented_hash` without treating them as validation failures.
    """
    return [m.group(0) for m in HASH_FRAGMENT_PATTERN.finditer(raw_text)]


def _bypass_hash_validation(raw_text: str, metrics: dict) -> bool:
    """If fragmented hash signatures are present, disable strict validation
    for this inspection and record the fragments verbatim. Returns whether
    strict validation stays enabled (False == bypass active)."""
    fragments = extract_fragmented_hashes(raw_text)
    if not fragments:
        return True
    metrics["raw_fragmented_hash"] = fragments
    return False


class NcUs1Parser:
    """NC US1: regex CPU/Memory extraction with >70% warning business rule."""

    def parse(self, raw_text: str) -> ParseResult:
        metrics: dict = {}
        strict = _bypass_hash_validation(raw_text, metrics)

        readings = {m.group(1).lower(): float(m.group(2)) for m in CPU_MEM_PATTERN.finditer(raw_text)}
        metrics["cpu_pct"] = readings.get("cpu")
        metrics["mem_pct"] = readings.get("memory")

        status = "NORMAL"
        if any(v is not None and v > RESOURCE_WARNING_THRESHOLD for v in readings.values()):
            status = "WARNING"

        return ParseResult(health_status=status, raw_metrics=metrics, strict_validation=strict)


class DaehanCableParser:
    """Daehan Cable: composite interface-error matrices -> JSONB array."""

    def parse(self, raw_text: str) -> ParseResult:
        metrics: dict = {}
        strict = _bypass_hash_validation(raw_text, metrics)

        errors = [
            {"interface": m.group("iface"), "error_type": m.group("err_type"), "count": int(m.group("count"))}
            for m in INTERFACE_ERROR_PATTERN.finditer(raw_text)
        ]
        metrics["interface_errors"] = errors

        status = "WARNING" if errors else "NORMAL"
        if any(e["count"] > 10000 for e in errors):
            status = "CRITICAL"

        return ParseResult(health_status=status, raw_metrics=metrics, strict_validation=strict)


class LgesParser:
    """LGES: column-centric tables via DataFrame.transpose(); unreachable
    hosts are marked INSPECTION_FAILED rather than raising."""

    def parse(self, raw_text: str) -> ParseResult:
        metrics: dict = {}
        strict = _bypass_hash_validation(raw_text, metrics)

        if "% connect timeout" in raw_text.lower() or "host unreachable" in raw_text.lower():
            metrics["error"] = "unreachable_host"
            return ParseResult(health_status="FAILED", raw_metrics=metrics, strict_validation=strict)

        rows = [line.split(",") for line in raw_text.strip().splitlines() if "," in line]
        if rows:
            df = pd.DataFrame(rows[1:], columns=rows[0]).transpose()
            metrics["columns"] = df.to_dict()

        return ParseResult(health_status="NORMAL", raw_metrics=metrics, strict_validation=strict)


class MbcNps24Parser:
    """MBC NPS24: OSPF neighbors + port-channel density -> hierarchical JSON."""

    OSPF_NEIGHBOR_PATTERN = re.compile(
        r"(?P<neighbor_id>\d+\.\d+\.\d+\.\d+)\s+\d+\s+(?P<state>\w+/\w+|\w+)\s+.*?(?P<iface>Et\d+/\d+|Po\d+)"
    )
    PORT_CHANNEL_PATTERN = re.compile(
        r"(?P<po>Po\d+).*?(?P<active>\d+)\s+active.*?(?P<total>\d+)\s+total", re.IGNORECASE
    )

    def parse(self, raw_text: str) -> ParseResult:
        metrics: dict = {}
        strict = _bypass_hash_validation(raw_text, metrics)

        metrics["ospf_neighbors"] = [
            {"neighbor_id": m.group("neighbor_id"), "state": m.group("state"), "interface": m.group("iface")}
            for m in self.OSPF_NEIGHBOR_PATTERN.finditer(raw_text)
        ]
        metrics["port_channels"] = [
            {"name": m.group("po"), "active": int(m.group("active")), "total": int(m.group("total"))}
            for m in self.PORT_CHANNEL_PATTERN.finditer(raw_text)
        ]

        status = "NORMAL"
        if any(n["state"].upper() not in ("FULL", "FULL/DR", "FULL/BDR") for n in metrics["ospf_neighbors"]):
            status = "WARNING"

        return ParseResult(health_status=status, raw_metrics=metrics, strict_validation=strict)


class DataExtractionFactory:
    """Selects the parser strategy for a given device profile / customer tag."""

    _REGISTRY: dict[str, type[DeviceLogParser]] = {
        "NC_US1": NcUs1Parser,
        "DAEHAN_CABLE": DaehanCableParser,
        "LGES": LgesParser,
        "MBC_NPS24": MbcNps24Parser,
    }

    @classmethod
    def get_parser(cls, profile_key: str) -> DeviceLogParser:
        try:
            return cls._REGISTRY[profile_key]()
        except KeyError as exc:
            raise ValueError(f"Unknown device log profile: {profile_key!r}") from exc

    @classmethod
    def parse(cls, profile_key: str, raw_text: str) -> ParseResult:
        return cls.get_parser(profile_key).parse(raw_text)
