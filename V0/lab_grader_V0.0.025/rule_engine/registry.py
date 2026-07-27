"""
Rule Plugin Registry — stage_id 하나가 곧 독립된 규칙 플러그인이다.
comparator 함수(engine/comparators/*.py, 이미 검증된 판정 로직)는 전혀 안 건드리고,
"이 stage는 이 comparator를 쓰고, collected 데이터는 이 키에서 온다"는 매핑만 등록한다.
rules.py는 이 registry를 순회할 뿐 stage 이름을 직접 하드코딩하지 않는다
(NTP/SNMP 같은 새 rule 추가 시 여기 register() 한 줄만 늘리면 됨).
"""

_REGISTRY = {}


def register(stage_id, category, compare_fn, collected_key):
    """stage_id: target_state.yaml의 최상위 키 (예: "stage_ntp")
    category: Finding.category에 쓰일 이름 (예: "NTP")
    compare_fn: comparator 함수 (checks, collected_data) -> [verdict, ...]
    collected_key: collected dict에서 이 stage 데이터를 찾을 키 (예: "ntp")
    """
    _REGISTRY[stage_id] = {
        "category": category, "compare_fn": compare_fn, "collected_key": collected_key,
    }


def get(stage_id):
    return _REGISTRY.get(stage_id)


def list_registered():
    return list(_REGISTRY.keys())


def _register_defaults():
    from engine.comparators.lacp import compare_lacp_stage
    from engine.comparators.mlag import compare_mlag_stage
    from engine.comparators.ospf import compare_ospf_stage
    from engine.comparators.vrrp import compare_vrrp_stage
    from engine.comparators.acl import compare_acl_stage
    from engine.comparators.ntp import compare_ntp_stage
    from engine.comparators.snmp import compare_snmp_stage

    register("stage_lacp", "LACP", compare_lacp_stage, "lacp")
    register("stage_mlag", "MLAG", compare_mlag_stage, "mlag")
    register("stage_ospf", "OSPF", compare_ospf_stage, "ospf")
    register("stage_vrrp", "VRRP", compare_vrrp_stage, "vrrp")
    register("stage_acl", "ACL", compare_acl_stage, "acl")
    register("stage_ntp", "NTP", compare_ntp_stage, "ntp")
    register("stage_snmp", "SNMP", compare_snmp_stage, "snmp")


_register_defaults()


if __name__ == "__main__":
    print("등록된 stage plugin 목록:", list_registered())
