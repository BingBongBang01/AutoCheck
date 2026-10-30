"""벤더별 점검 명령 — 카탈로그 항목의 체크ID 를 플랫폼마다 같은 목적의 명령으로 바꾼다(IMPROVEMENT_PLAN 4-5).

카탈로그(commands_catalog_default.xlsx)는 Arista EOS 명령으로 쓰여 있다. 체크ID(version_info, cpu_usage …)는 '무엇을
확인하는 명령인지'라서, Cisco 장비에는 같은 체크ID 의 Cisco 명령을 보낸다. Arista 는 카탈로그 명령을 그대로 쓰고, 체크ID 가
없거나 이 표에 없는 항목(사용자가 더한 명령)은 모든 장비에 그 글자 그대로 보낸다 — 무엇을 보낼지 사용자가 정한 명령이다.

이 표는 Cisco 명령 레퍼런스에 있는 명령만으로 만들었고 실제 장비 출력으로는 확인하지 않았다(장비 없음).
"""
from core import vendors
from core.command_policy import blocked_reason
from core.vendors import ARISTA_EOS, CISCO_IOS, CISCO_NXOS, CISCO_XE

# 카탈로그(EOS) 명령 대신 체크ID 로 고른 명령을 보내는 플랫폼
TRANSLATED_PLATFORMS = (vendors.CISCO_IOS, vendors.CISCO_XE, vendors.CISCO_NXOS)

# 체크ID -> (Cisco IOS, Cisco NX-OS[, Cisco IOS-XE]) — IOS-XE 를 따로 적지 않으면 IOS 와 같다.
# 값: 문자열(명령 하나)·튜플(여러 명령)·None(그 플랫폼엔 같은 목적의 명령이 없거나, 다른 명령의 출력에 이미 들어 있다).
# NX-OS 는 'show interface …'(단수)만 받고, 'vrf all' 로 모든 VRF 를 한 번에 본다.
_CISCO = {
    "privilege_mode": ("enable", "enable"),
    "disable_paging": ("terminal length 0", "terminal length 0"),
    "version_info": ("show version", "show version"),
    "clock_status": ("show clock", "show clock"),
    "ntp_status": ("show ntp status", "show ntp peer-status"),
    "reload_cause": (None, "show system reset-reason"),                     # IOS·IOS-XE 는 show version 에 있다
    "inventory_status": ("show inventory", "show inventory"),
    "module_status": ("show module", "show module"),
    "power_status": ("show environment all", "show environment power"),     # IOS 는 전원·팬·온도를 한 번에
    "cooling_status": (None, "show environment fan"),
    "temperature_status": (None, "show environment temperature"),
    "cpu_usage": ("show processes cpu sorted", "show system resources"),    # NX-OS 는 CPU·메모리를 한 번에
    "memory_usage": ("show processes memory sorted", None),
    "log_check": ("show logging", "show logging logfile"),
    "interface_status": ("show interfaces status", "show interface status"),
    "interface_brief": ("show ip interface brief", "show ip interface brief vrf all"),
    "interface_errors": ("show interfaces counters errors", "show interface counters errors"),
    "interface_queue": (None, None),
    "interface_rates": ("show interfaces summary", None),
    "interface_transceiver": ("show interfaces transceiver", "show interface transceiver details"),
    "interface_description": ("show interfaces description", "show interface description"),
    # Cisco 는 CDP 가 기본이고 LLDP 는 켜야 돈다 — 둘 다 받는다(토폴로지가 둘 다 읽는다).
    "lldp_neighbors": (("show cdp neighbors detail", "show lldp neighbors detail"),
                       ("show cdp neighbors detail", "show lldp neighbors detail")),
    "errdisable_status": (("show interfaces status err-disabled", "show errdisable recovery"),
                          "show interface status err-disabled"),
    "port_channel_status": ("show etherchannel summary", "show port-channel summary"),
    "po10_status": (None, None),                                            # 포트채널 요약에 들어 있다
    "lacp_eth3": ("show lacp neighbor", "show lacp neighbor"),
    "mlag_status": (None, "show vpc"),                                      # MLAG 에 해당하는 것은 NX-OS vPC
    "mlag_detail": (None, ("show vpc peer-keepalive", "show vpc consistency-parameters global")),
    "mlag_interfaces": (None, None),                                        # vPC 멤버는 show vpc 에 들어 있다
    "vrrp_status": (("show standby brief", "show vrrp brief"), ("show hsrp brief", "show vrrp")),
    "varp_status": (None, None),                                            # Arista VARP 전용
    "stp_status": ("show spanning-tree", "show spanning-tree"),
    "stp_detail": ("show spanning-tree detail", "show spanning-tree detail"),
    "acl_status": ("show ip access-lists", "show ip access-lists"),
    "arp_status": ("show ip arp", "show ip arp vrf all"),
    "route_summary": ("show ip route summary", "show ip route summary vrf all"),
    "bgp_summary": ("show ip bgp summary", "show ip bgp summary vrf all"),
    "evpn_summary": (None, "show bgp l2vpn evpn summary", "show bgp l2vpn evpn summary"),
    "vxlan_status": (None, ("show nve vni", "show nve peers"), ("show nve vni", "show nve peers")),
    "ospf_neighbor": ("show ip ospf neighbor", "show ip ospf neighbors vrf all"),
    "ospf_interface": ("show ip ospf interface brief", "show ip ospf interface brief vrf all"),
    "bgp_routes": ("show ip route bgp", "show ip route bgp"),
    "vrf_bgp_routes": (None, "show ip route bgp vrf all"),
    "ospf_routes": ("show ip route ospf", "show ip route ospf"),
    "static_routes": ("show ip route static", "show ip route static vrf all"),
    "static_route_config": ("show running-config | include ip route", "show running-config | include ip route"),
    "running_config": ("show running-config", "show running-config"),
    "startup_config": ("show startup-config", "show startup-config"),
}


def platform_commands(item, platform):
    """카탈로그 항목 하나 -> 그 플랫폼에 보낼 명령 목록. 같은 목적의 명령이 없으면 빈 목록."""
    row = _CISCO.get(item.get("check_id")) if platform in TRANSLATED_PLATFORMS else None
    if row is None:
        return [item["command"]]
    ios, nxos, *xe = row
    value = {vendors.CISCO_IOS: ios, vendors.CISCO_XE: xe[0] if xe else ios, vendors.CISCO_NXOS: nxos}[platform]
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def commands_for(catalog, platform):
    """카탈로그 -> (보낼 명령, [(보내지 않을 명령, 읽기 전용 정책 이유)], [그 플랫폼에 같은 목적의 명령이 없는 카탈로그 명령]).

    켜진 항목만, 같은 명령은 한 번만 보낸다. Arista 는 카탈로그 명령 그대로다(겹친 명령만 뺀 split_enabled_commands)."""
    send, skipped, unmapped = [], [], []
    for item in catalog:
        if not item.get("enabled"):
            continue
        commands = platform_commands(item, platform)
        if not commands:
            unmapped.append(item["command"])
        for command in commands:
            reason = blocked_reason(command)
            if reason:
                skipped.append((command, reason))
            elif command not in send:
                send.append(command)
    return send, skipped, unmapped


# ---------- 보고서 판정: 양식의 점검 명령(EOS) -> Cisco 로그에서 같은 내용을 담은 명령 ----------
# 정기점검 양식(inspection_report_template.yaml)의 점검 항목은 EOS 명령을 부분 일치 패턴으로 적는다. 사용자 PC 에는
# 예전 버전이 복사해 둔 양식 사본이 있고 그 commands 가 번들 값을 이기므로, Cisco 명령을 양식에 더하는 것만으로는
# 반영되지 않는다 — 그래서 여기서 잇는다. 키는 양식의 패턴(소문자), 평가기마다 다르게 읽는 패턴은 (패턴, 평가기).
# 값의 패턴은 위 _CISCO 가 실제로 보내는 명령의 부분 문자열이어야 한다(테스트가 확인한다).
# Arista 도 있다 — 새 EOS 는 show environment cooling·temperature 를 거부한다('deprecated by show system environment …',
# 실장비 25대 중 25·22대). 사용자 PC 의 예전 양식 사본에도 새 명령이 걸리게 여기서 잇는다(기본 카탈로그도 보낸다).
_REPORT_ALIASES = {
    "show environment cooling": {CISCO_NXOS: ("show environment fan",), ARISTA_EOS: ("show system environment cooling",)},
    "show environment temperature": {ARISTA_EOS: ("show system environment temperature",)},
    ("show processes top", "cpu"): {CISCO_IOS: ("show processes cpu",), CISCO_XE: ("show processes cpu",),
                                     CISCO_NXOS: ("show system resources",)},
    ("show processes top", "memory_free"): {CISCO_IOS: ("show processes memory",),
                                             CISCO_XE: ("show processes memory",),
                                             CISCO_NXOS: ("show system resources",)},
    "show interfaces counters errors": {CISCO_NXOS: ("show interface counters errors",)},
    "show interfaces transceiver": {CISCO_NXOS: ("show interface transceiver",)},
    "show port-channel dense": {CISCO_IOS: ("show etherchannel summary",), CISCO_XE: ("show etherchannel summary",)},
    "show mlag": {CISCO_NXOS: ("show vpc",)},
    # Cisco 의 게이트웨이 이중화는 대개 HSRP 다 — VRRP 보다 먼저 본다.
    "show ip virtual-router": {CISCO_IOS: ("show standby brief",), CISCO_XE: ("show standby brief",),
                               CISCO_NXOS: ("show hsrp brief",)},
    "show vrrp brief": {CISCO_NXOS: ("show vrrp",)},
    "show bgp evpn summary": {CISCO_XE: ("show bgp l2vpn evpn summary",),
                              CISCO_NXOS: ("show bgp l2vpn evpn summary",)},
    "show interfaces vxlan 1": {CISCO_XE: ("show nve peers",), CISCO_NXOS: ("show nve peers",)},
}
# 그 플랫폼에는 없는 기능 — 항목의 패턴이 모두 여기 들면 '미수집'이 아니라 '해당없음'이다.
_NOT_ON = {
    "show mlag": {CISCO_IOS: "MLAG 가 없습니다(NX-OS 는 vPC)", CISCO_XE: "MLAG 가 없습니다(NX-OS 는 vPC)"},
    "show bgp evpn summary": {CISCO_IOS: "EVPN 을 지원하지 않습니다"},
    "show interfaces vxlan 1": {CISCO_IOS: "VXLAN 을 지원하지 않습니다"},
}


def report_patterns(patterns, evaluator, platform):
    """점검 항목의 명령 패턴 -> 그 플랫폼 로그에서 찾을 패턴(양식 순서를 지키고, 각 패턴 바로 뒤에 대응 명령 — Cisco 의
    같은 내용 명령, 새 EOS 의 바뀐 명령). 모르는 플랫폼은 양식 그대로."""
    out = []
    for pattern in patterns or []:
        out.append(pattern)
        key = str(pattern).lower()
        aliases = _REPORT_ALIASES.get((key, evaluator)) or _REPORT_ALIASES.get(key) or {}
        out.extend(a for a in aliases.get(platform, ()) if a not in out)
    return out


def not_applicable(patterns, platform):
    """항목의 패턴이 모두 그 플랫폼에 없는 기능이면 이유(문장), 아니면 None."""
    reasons = [(_NOT_ON.get(str(p).lower()) or {}).get(platform) for p in patterns or []]
    if reasons and all(reasons):
        return f"{vendors.label(platform)}에는 {reasons[0]}"
    return None


def commands_by_platform(catalog):
    """명령을 바꿔 보내는 플랫폼마다 보낼 명령 — {플랫폼 id: [명령]} (collector.collect_all 의 commands_by_platform).
    Arista 는 넣지 않는다 — 카탈로그 명령(split_enabled_commands)을 예전 그대로 보낸다."""
    return {platform: commands_for(catalog, platform)[0] for platform in TRANSLATED_PLATFORMS}
