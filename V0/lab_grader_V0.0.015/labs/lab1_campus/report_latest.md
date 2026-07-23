# lab1_campus 점검 보고서
작성일: 2026-07-23

## Executive Summary
전체 0/18건 PASS(0%). 미해결 단계: VLAN.
(분석 출처: rule_based)

## 단계별 결과
| 단계 | 상태 | PASS | TOTAL |
|---|---|---|---|
| VLAN | IN_PROGRESS | 0 | 18 |
| STP | SKIPPED | 0 | 14 |
| LACP | SKIPPED | 0 | 5 |
| MLAG | SKIPPED | 0 | 2 |
| OSPF | SKIPPED | 0 | 2 |
| VRRP | SKIPPED | 0 | 2 |
| ACL | SKIPPED | 0 | 4 |

## 특이사항 상세
### VLAN
- **vlan_100_exists__Access1** (Access1)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_100_exists__Access2** (Access2)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_100_exists__Agg1** (Agg1)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_100_exists__Agg2** (Agg2)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_100_exists__Core1** (Core1)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_100_exists__Core2** (Core2)  기대값: VLAN 100 존재 / 실제: 존재하지 않음
- **vlan_200_exists__Access3** (Access3)  기대값: VLAN 200 존재 / 실제: 존재하지 않음
- **vlan_200_exists__Agg1** (Agg1)  기대값: VLAN 200 존재 / 실제: 존재하지 않음
- **vlan_200_exists__Agg2** (Agg2)  기대값: VLAN 200 존재 / 실제: 존재하지 않음
- **vlan_200_exists__Core1** (Core1)  기대값: VLAN 200 존재 / 실제: 존재하지 않음
- **vlan_200_exists__Core2** (Core2)  기대값: VLAN 200 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Core1** (Core1)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Core2** (Core2)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Agg1** (Agg1)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Agg2** (Agg2)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Access1** (Access1)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Access2** (Access2)  기대값: VLAN 999 존재 / 실제: 존재하지 않음
- **vlan_999_exists__Access3** (Access3)  기대값: VLAN 999 존재 / 실제: 존재하지 않음

### STP
- **root_priority_vlan1_core1** (Core1)  기대값: 4096 / 실제: None
- **root_priority_vlan1_core2** (Core2)  기대값: 8192 / 실제: None
- **root_priority_vlan100_agg1** (Agg1)  기대값: 4096 / 실제: None
- **root_priority_vlan200_agg1** (Agg1)  기대값: 4096 / 실제: None
- **root_priority_vlan999_agg1** (Agg1)  기대값: 4096 / 실제: None
- **root_priority_vlan100_agg2** (Agg2)  기대값: 8192 / 실제: None
- **root_priority_vlan200_agg2** (Agg2)  기대값: 8192 / 실제: None
- **root_priority_vlan999_agg2** (Agg2)  기대값: 8192 / 실제: None
- **no_stp_priority_on_access1** (Access1)  기대값: 모든 VLAN priority = 기본값(32768) 또는 미설정 / 실제: 수집된 STP 데이터 없음(장비 접속 실패 또는 커맨드 누락 가능성)
- **no_stp_priority_on_access2** (Access2)  기대값: 모든 VLAN priority = 기본값(32768) 또는 미설정 / 실제: 수집된 STP 데이터 없음(장비 접속 실패 또는 커맨드 누락 가능성)
- **no_stp_priority_on_access3** (Access3)  기대값: 모든 VLAN priority = 기본값(32768) 또는 미설정 / 실제: 수집된 STP 데이터 없음(장비 접속 실패 또는 커맨드 누락 가능성)
- **actual_root_bridge_vlan1** ((network-wide))  기대값: Core1 / 실제: root로 표시된 장비 없음(STP 미수렴 중이거나 데이터 누락 가능성)
- **actual_root_bridge_vlan100** ((network-wide))  기대값: Agg1 / 실제: root로 표시된 장비 없음(STP 미수렴 중이거나 데이터 누락 가능성)
- **actual_root_bridge_vlan200** ((network-wide))  기대값: Agg1 / 실제: root로 표시된 장비 없음(STP 미수렴 중이거나 데이터 누락 가능성)

### LACP
- **lacp_no_degraded_agg1__Agg1** (Agg1)  기대값: 모든 Port-Channel 멤버 Bundled(P) / 실제: 수집된 LACP 데이터 없음
- **lacp_no_degraded_agg2__Agg2** (Agg2)  기대값: 모든 Port-Channel 멤버 Bundled(P) / 실제: 수집된 LACP 데이터 없음
- **lacp_no_degraded_access1__Access1** (Access1)  기대값: 모든 Port-Channel 멤버 Bundled(P) / 실제: 수집된 LACP 데이터 없음
- **lacp_no_degraded_access2__Access2** (Access2)  기대값: 모든 Port-Channel 멤버 Bundled(P) / 실제: 수집된 LACP 데이터 없음
- **lacp_no_degraded_access3__Access3** (Access3)  기대값: 모든 Port-Channel 멤버 Bundled(P) / 실제: 수집된 LACP 데이터 없음

### MLAG
- **mlag_active_agg1__Agg1** (Agg1)  기대값: state=Active, negotiation=Connected / 실제: 수집된 MLAG 데이터 없음
- **mlag_active_agg2__Agg2** (Agg2)  기대값: state=Active, negotiation=Connected / 실제: 수집된 MLAG 데이터 없음

### OSPF
- **ospf_all_full_core1__Core1** (Core1)  기대값: OSPF 네이버 FULL / 실제: 수집된 OSPF 네이버 데이터 없음
- **ospf_all_full_core2__Core2** (Core2)  기대값: OSPF 네이버 FULL / 실제: 수집된 OSPF 네이버 데이터 없음

### VRRP
- **vrrp_single_master_vlan100__Vlan100** ((group))  기대값: Vlan100의 Master는 정확히 1대 / 실제: 수집된 VRRP 데이터 없음(대상: ['Agg1', 'Agg2'])
- **vrrp_single_master_vlan200__Vlan200** ((group))  기대값: Vlan200의 Master는 정확히 1대 / 실제: 수집된 VRRP 데이터 없음(대상: ['Agg1', 'Agg2'])

### ACL
- **acl_mgmt_exists_core1__Core1** (Core1)  기대값: ACL MGMT-ACL 설정 확인 / 실제: 수집된 ACL 데이터 없음
- **acl_mgmt_exists_core2__Core2** (Core2)  기대값: ACL MGMT-ACL 설정 확인 / 실제: 수집된 ACL 데이터 없음
- **acl_mgmt_explicit_deny_core1__Core1** (Core1)  기대값: ACL MGMT-ACL 설정 확인 / 실제: 수집된 ACL 데이터 없음
- **acl_mgmt_explicit_deny_core2__Core2** (Core2)  기대값: ACL MGMT-ACL 설정 확인 / 실제: 수집된 ACL 데이터 없음

## 조치 권고 (우선순위 순)
1. **(network-wide) / actual_root_bridge_vlan1** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
2. **(network-wide) / actual_root_bridge_vlan100** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
3. **(network-wide) / actual_root_bridge_vlan200** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
4. **Agg1 / vlan_100_exists__Agg1** — VLAN 설정 누락 — 해당 장비에 VLAN 생성 여부 확인
5. **Agg2 / vlan_100_exists__Agg2** — VLAN 설정 누락 — 해당 장비에 VLAN 생성 여부 확인
