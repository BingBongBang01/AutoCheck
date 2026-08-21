"""
Device Inventory — 프로젝트의 장비 정보(IP·계정·역할·벤더 등)를 관리하는 유일한 출처.

전체 코드베이스가 `from engine import device_inventory as di` 형태로 이 모듈 전체를
가져다 di.load_inventory(...) 식으로 쓰므로, 실제 구현은 세 파일로 분리하되 이 파일에서
이름을 그대로 재노출해 호출부는 전혀 바뀌지 않는다:
  - device_inventory_core.py: 스키마/로드/저장/CRUD/IP Pool 자동할당
  - device_inventory_import_export.py: CSV/YAML/JSON/Excel 불러오기·엑셀 내보내기
  - device_inventory_reachability.py: 도달가능성(포트 체크)
"""
from engine.device_inventory_core import (
    DEVICE_FIELDS, DEFAULT_DEVICE, DEFAULT_PROJECT_DEFAULTS, normalize_device,
    load_inventory, save_inventory, resolve_credentials, get_enabled_devices,
    add_device, remove_device, update_device, generate_ip_pool, auto_allocate_ips,
)
from engine.device_inventory_import_export import (
    import_csv, import_yaml, import_json, import_excel, merge_imported,
    copy_from_inventory, export_to_excel, EXCEL_HEADERS,
)
from engine.device_inventory_reachability import check_reachability

__all__ = [
    "DEVICE_FIELDS", "DEFAULT_DEVICE", "DEFAULT_PROJECT_DEFAULTS", "normalize_device",
    "load_inventory", "save_inventory", "resolve_credentials", "get_enabled_devices",
    "add_device", "remove_device", "update_device", "generate_ip_pool", "auto_allocate_ips",
    "import_csv", "import_yaml", "import_json", "import_excel", "merge_imported",
    "copy_from_inventory", "export_to_excel", "EXCEL_HEADERS", "check_reachability",
]
