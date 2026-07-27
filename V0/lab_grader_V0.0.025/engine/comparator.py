"""
target_state.yaml의 체크 항목을 실제 파싱 결과(collected)와 대조한다.
check_type: exact_match / in_set / absence 3종만 우선 구현 (numeric_threshold는 로드맵).

단계(stage)별 구현은 engine/comparators/ 아래에 커맨드 파서(parsers/)와 동일한 구조로
분리되어 있다: vlan.py / stp.py / lacp.py / mlag.py / ospf.py / vrrp.py / acl.py.
이 파일은 기존 호출부(rule_engine/rules.py, main.py)와의 호환을 위한 파사드.
"""
from engine.comparators.vlan import compare_vlan_stage
from engine.comparators.stp import compare_stp_stage, build_vlan_index, EOS_DEFAULT_BRIDGE_PRIORITY
from engine.comparators.lacp import compare_lacp_stage
from engine.comparators.mlag import compare_mlag_stage
from engine.comparators.ospf import compare_ospf_stage
from engine.comparators.vrrp import compare_vrrp_stage
from engine.comparators.acl import compare_acl_stage
from engine.comparators.ntp import compare_ntp_stage
from engine.comparators.snmp import compare_snmp_stage

__all__ = [
    "compare_vlan_stage", "compare_stp_stage", "build_vlan_index", "EOS_DEFAULT_BRIDGE_PRIORITY",
    "compare_lacp_stage", "compare_mlag_stage", "compare_ospf_stage", "compare_vrrp_stage", "compare_acl_stage",
    "compare_ntp_stage", "compare_snmp_stage",
]
