"""
VendorDriver — check_id(벤더 무관 추상 ID)를 실제 CLI 커맨드로 변환하는 책임.
Collector/Parser/Command Catalog가 "show vlan brief" 같은 리터럴을 직접 알면 안 되고,
전부 VendorDriver를 거쳐야 한다는 게 이번 리팩토링의 핵심.
"""
from abc import ABC, abstractmethod


class VendorDriver(ABC):
    vendor_name: str = "generic"
    netmiko_device_type: str = "generic"

    @abstractmethod
    def command_for(self, check_id: str) -> str:
        """check_id -> 실제 CLI 문자열. 지원 안 하면 None 리턴(예외 대신)."""
        raise NotImplementedError

    @abstractmethod
    def supported_check_ids(self) -> list:
        raise NotImplementedError

    def reverse_command_map(self) -> dict:
        """실제 CLI 문자열 -> check_id. ParserStep이 raw CLI 텍스트에서 check_id를
        역으로 찾아 registry 기반 파서를 고를 수 있게 해준다(벤더별 하드코딩 제거)."""
        return {self.command_for(cid): cid for cid in self.supported_check_ids() if self.command_for(cid)}


_REGISTRY = {}


def register(driver: VendorDriver):
    _REGISTRY[driver.vendor_name] = driver


def get_driver(vendor_name: str) -> VendorDriver:
    driver = _REGISTRY.get(vendor_name.lower())
    if not driver:
        raise ValueError(f"등록된 VendorDriver 없음: {vendor_name} (지원: {list(_REGISTRY.keys())})")
    return driver


def list_vendors():
    return list(_REGISTRY.keys())
