"""
검사 커맨드 카탈로그 관리.
- 필수(essential): 기본 활성화(enabled=True), 어떤 장비 점검이든 공통으로 쓰는 것
- 선택(optional): 기본 비활성화(enabled=False), 상황에 따라 켜서 쓰는 것
- custom: 사용자가 직접 추가한 것

로컬 파일(commands_catalog.yaml)에 저장/불러오기 — 토글·추가·삭제한 상태가 다음 실행 때도 유지됨.
"""
import os
import yaml

DEFAULT_CATALOG_PATH = "config/commands_catalog.yaml"

# 최초 실행 시(파일이 없을 때) 채워 넣을 기본값.
# 근거: 실제 회사 정기점검 보고서 4종 + EOS 매뉴얼 대조 분석 결과.
DEFAULT_ESSENTIAL = [
    {"command": "show version", "description": "가동시간·모델 확인", "enabled": True},
    {"command": "show environment power", "description": "전원 이중화 확인", "enabled": True},
    {"command": "show environment cooling", "description": "팬 상태 확인", "enabled": True},
    {"command": "show environment temperature", "description": "온도 이상 확인", "enabled": True},
    {"command": "show processes top once", "description": "CPU/메모리 사용률", "enabled": True},
    {"command": "show log", "description": "특이 로그 확인", "enabled": True},
    {"command": "show interface status", "description": "포트 링크 상태", "enabled": True},
    {"command": "show interfaces counters errors", "description": "CRC/에러 카운터", "enabled": True},
    {"command": "show interfaces transceiver", "description": "광모듈 정상범위", "enabled": True},
    {"command": "show ip interface brief", "description": "인터페이스 요약", "enabled": True},
    {"command": "show clock", "description": "시간 동기화 확인", "enabled": True},
]

DEFAULT_OPTIONAL = [
    {"command": "show module", "description": "섀시형 장비 모듈 확인", "enabled": False},
    {"command": "show port-channel summary", "description": "LACP 이중화 확인", "enabled": False},
    {"command": "show mlag", "description": "MLAG 페어 상태", "enabled": False},
    {"command": "show vrrp brief", "description": "VRRP 이중화 확인", "enabled": False},
    {"command": "show ip virtual-router", "description": "VARP 이중화 확인", "enabled": False},
    {"command": "show spanning-tree vlan 1,100,200,999", "description": "STP 루트 확인", "enabled": False},
    {"command": "show bgp evpn summary", "description": "EVPN 네이버 확인", "enabled": False},
    {"command": "show ip bgp summary", "description": "BGP 네이버 확인", "enabled": False},
    {"command": "show ip ospf neighbor", "description": "OSPF 네이버 확인", "enabled": False},
    {"command": "show ip arp vrf all", "description": "ARP 상태 수집", "enabled": False},
    {"command": "show inventory", "description": "S/N·부품 확인", "enabled": False},
    {"command": "show running-config", "description": "설정 변경 이력 대조용", "enabled": False},
    {"command": "show reload cause", "description": "마지막 재부팅 원인", "enabled": False},
    {"command": "show ntp status", "description": "NTP 동기화 확인", "enabled": False},
    {"command": "show interfaces counters rates", "description": "실시간 트래픽량", "enabled": False},
    {"command": "show interfaces description", "description": "포트 라벨 확인", "enabled": False},
]


def _make_default_catalog():
    catalog = []
    for i, item in enumerate(DEFAULT_ESSENTIAL):
        catalog.append({"id": f"essential_{i}", "category": "essential", **item})
    for i, item in enumerate(DEFAULT_OPTIONAL):
        catalog.append({"id": f"optional_{i}", "category": "optional", **item})
    return catalog


def load_catalog(path=DEFAULT_CATALOG_PATH):
    if not os.path.exists(path):
        catalog = _make_default_catalog()
        save_catalog(catalog, path)
        return catalog
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def save_catalog(catalog, path=DEFAULT_CATALOG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(catalog, f, allow_unicode=True, sort_keys=False)


def toggle_command(catalog, command_id, enabled):
    for item in catalog:
        if item["id"] == command_id:
            item["enabled"] = enabled
            return True
    return False


def add_command(catalog, command_text, description=""):
    existing_ids = [item["id"] for item in catalog if item["category"] == "custom"]
    next_num = len(existing_ids)
    new_id = f"custom_{next_num}"
    while new_id in existing_ids:
        next_num += 1
        new_id = f"custom_{next_num}"
    catalog.append({
        "id": new_id, "category": "custom",
        "command": command_text, "description": description or "(설명 없음)",
        "enabled": True,
    })
    return new_id


def remove_command(catalog, command_id):
    idx = next((i for i, item in enumerate(catalog) if item["id"] == command_id), None)
    if idx is None:
        return False
    del catalog[idx]
    return True


def get_enabled_commands(catalog):
    return [item["command"] for item in catalog if item["enabled"]]


if __name__ == "__main__":
    catalog = load_catalog()
    print(f"카탈로그 항목 {len(catalog)}개 (필수 {sum(1 for c in catalog if c['category']=='essential')}, "
          f"선택 {sum(1 for c in catalog if c['category']=='optional')}, "
          f"커스텀 {sum(1 for c in catalog if c['category']=='custom')})")
    print("\n활성화된 커맨드:")
    for cmd in get_enabled_commands(catalog):
        print(" -", cmd)
