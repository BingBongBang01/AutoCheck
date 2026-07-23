"""Async network raw CLI collector using asyncio and scrapli."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from scrapli.driver.core import AsyncEOSDriver, AsyncIOSXEDriver
from scrapli.exceptions import ScrapliException

ASYNC_DRIVERS = {
    "arista_eos": AsyncEOSDriver,
    "cisco_iosxe": AsyncIOSXEDriver,
}

DEFAULT_TRANSPORT = "asyncssh"
DEFAULT_TIMEOUT_SOCKET = 15
DEFAULT_TIMEOUT_TRANSPORT = 15
DEFAULT_TIMEOUT_OPS = 30


@dataclass
class DeviceTarget:
    """Connection parameters for a single managed device."""

    hostname: str
    host: str
    device_type: str
    username: str
    password: str
    port: int = 22
    auth_strict_key: bool = False
    extra_options: Dict[str, Any] = field(default_factory=dict)


class AsyncNetworkCollector:
    """Concurrently collects raw CLI output from a fleet of network devices."""

    def __init__(self, max_concurrency: int = 15, command_timeout: int = DEFAULT_TIMEOUT_OPS) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._command_timeout = command_timeout

    async def _collect_one(self, device: DeviceTarget, command: str) -> Dict[str, Any]:
        start = time.monotonic()
        driver_cls = ASYNC_DRIVERS.get(device.device_type)

        if driver_cls is None:
            return {
                "hostname": device.hostname,
                "success": False,
                "result": f"Unsupported device_type: {device.device_type}",
                "execution_time_sec": round(time.monotonic() - start, 4),
            }

        conn_args = {
            "host": device.host,
            "auth_username": device.username,
            "auth_password": device.password,
            "auth_strict_key": device.auth_strict_key,
            "port": device.port,
            "transport": DEFAULT_TRANSPORT,
            "timeout_socket": DEFAULT_TIMEOUT_SOCKET,
            "timeout_transport": DEFAULT_TIMEOUT_TRANSPORT,
            "timeout_ops": self._command_timeout,
            **device.extra_options,
        }

        async with self._semaphore:
            try:
                driver = driver_cls(**conn_args)
                await driver.open()
                try:
                    response = await driver.send_command(command)
                    return {
                        "hostname": device.hostname,
                        "success": not response.failed,
                        "result": response.result if not response.failed else response.result or "command failed",
                        "execution_time_sec": round(time.monotonic() - start, 4),
                    }
                finally:
                    await driver.close()
            except ScrapliException as exc:
                return {
                    "hostname": device.hostname,
                    "success": False,
                    "result": f"ScrapliException: {exc}",
                    "execution_time_sec": round(time.monotonic() - start, 4),
                }
            except ConnectionError as exc:
                return {
                    "hostname": device.hostname,
                    "success": False,
                    "result": f"ConnectionError: {exc}",
                    "execution_time_sec": round(time.monotonic() - start, 4),
                }
            except Exception as exc:  # noqa: BLE001 - surface unexpected errors per-device without aborting the batch
                return {
                    "hostname": device.hostname,
                    "success": False,
                    "result": f"UnexpectedError: {exc}",
                    "execution_time_sec": round(time.monotonic() - start, 4),
                }

    async def collect_all(self, devices: List[DeviceTarget], command: str) -> List[Dict[str, Any]]:
        """Gather raw CLI output from every device concurrently, bounded by the semaphore."""
        tasks = [self._collect_one(device, command) for device in devices]
        return await asyncio.gather(*tasks)


import pytest
from unittest.mock import AsyncMock, patch

def make_device(hostname: str = "sw1", device_type: str = "arista_eos") -> DeviceTarget:
    return DeviceTarget(
        hostname=hostname,
        host="192.0.2.10",
        device_type=device_type,
        username="admin",
        password="admin",
    )

class FakeResponse:
    def __init__(self, result: str, failed: bool = False) -> None:
        self.result = result
        self.failed = failed

@pytest.mark.asyncio
async def test_collect_all_success():
    device = make_device()
    fake_driver = AsyncMock()
    fake_driver.open = AsyncMock()
    fake_driver.close = AsyncMock()
    fake_driver.send_command = AsyncMock(return_value=FakeResponse("Ethernet1 is up"))

    with patch.dict(ASYNC_DRIVERS, {"arista_eos": lambda **kwargs: fake_driver}):
        collector = AsyncNetworkCollector(max_concurrency=5)
        results = await collector.collect_all([device], "show interfaces status")

    assert len(results) == 1
    assert results[0]["hostname"] == "sw1"
    assert results[0]["success"] is True
    assert results[0]["result"] == "Ethernet1 is up"
    assert isinstance(results[0]["execution_time_sec"], float)

@pytest.mark.asyncio
async def test_collect_all_scrapli_exception():
    device = make_device()
    fake_driver = AsyncMock()
    fake_driver.open = AsyncMock(side_effect=ScrapliException("auth failed"))
    fake_driver.close = AsyncMock()

    with patch.dict(ASYNC_DRIVERS, {"arista_eos": lambda **kwargs: fake_driver}):
        collector = AsyncNetworkCollector()
        results = await collector.collect_all([device], "show version")

    assert results[0]["success"] is False
    assert "ScrapliException" in results[0]["result"]

@pytest.mark.asyncio
async def test_collect_all_connection_error():
    device = make_device()
    fake_driver = AsyncMock()
    fake_driver.open = AsyncMock(side_effect=ConnectionError("unreachable"))
    fake_driver.close = AsyncMock()

    with patch.dict(ASYNC_DRIVERS, {"arista_eos": lambda **kwargs: fake_driver}):
        collector = AsyncNetworkCollector()
        results = await collector.collect_all([device], "show version")

    assert results[0]["success"] is False
    assert "ConnectionError" in results[0]["result"]

@pytest.mark.asyncio
async def test_collect_all_unsupported_device_type():
    device = make_device(device_type="juniper_junos")
    collector = AsyncNetworkCollector()
    results = await collector.collect_all([device], "show version")

    assert results[0]["success"] is False
    assert "Unsupported device_type" in results[0]["result"]

@pytest.mark.asyncio
async def test_collect_all_concurrency_limit_respected():
    devices = [make_device(hostname=f"sw{i}") for i in range(10)]
    in_flight = 0
    max_in_flight = 0

    async def fake_open():
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)

    async def fake_close():
        nonlocal in_flight
        in_flight -= 1

    fake_driver = AsyncMock()
    fake_driver.open = AsyncMock(side_effect=fake_open)
    fake_driver.close = AsyncMock(side_effect=fake_close)
    fake_driver.send_command = AsyncMock(return_value=FakeResponse("ok"))

    with patch.dict(ASYNC_DRIVERS, {"arista_eos": lambda **kwargs: fake_driver}):
        collector = AsyncNetworkCollector(max_concurrency=3)
        await collector.collect_all(devices, "show version")

    assert max_in_flight <= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
