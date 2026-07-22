# lab1_campus 점검 보고서
작성일: 2026-07-22

## Executive Summary
전체 21/32건 PASS(66%). 완료 단계: VLAN. 미해결 단계: STP.
(분석 출처: rule_based)

## 단계별 결과
| 단계 | 상태 | PASS | TOTAL |
|---|---|---|---|
| VLAN | COMPLETE | 18 | 18 |
| STP | IN_PROGRESS | 3 | 14 |
| LACP | SKIPPED | 0 | 0 |
| MLAG | SKIPPED | 0 | 0 |
| OSPF | SKIPPED | 0 | 0 |
| VRRP | SKIPPED | 0 | 0 |
| ACL | SKIPPED | 0 | 0 |

## 특이사항 상세
### STP
- **root_priority_vlan1_core1** (Core1)  기대값: 4096 / 실제: 32768
- **root_priority_vlan1_core2** (Core2)  기대값: 8192 / 실제: 32768
- **root_priority_vlan100_agg1** (Agg1)  기대값: 4096 / 실제: 32768
- **root_priority_vlan200_agg1** (Agg1)  기대값: 4096 / 실제: 32768
- **root_priority_vlan999_agg1** (Agg1)  기대값: 4096 / 실제: 32768
- **root_priority_vlan100_agg2** (Agg2)  기대값: 8192 / 실제: 32768
- **root_priority_vlan200_agg2** (Agg2)  기대값: 8192 / 실제: 32768
- **root_priority_vlan999_agg2** (Agg2)  기대값: 8192 / 실제: 32768
- **actual_root_bridge_vlan1** ((network-wide))  기대값: Core1 / 실제: Core2
- **actual_root_bridge_vlan100** ((network-wide))  기대값: Agg1 / 실제: Access1
- **actual_root_bridge_vlan200** ((network-wide))  기대값: Agg1 / 실제: Access3

## 조치 권고 (우선순위 순)
1. **(network-wide) / actual_root_bridge_vlan1** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
2. **(network-wide) / actual_root_bridge_vlan100** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
3. **(network-wide) / actual_root_bridge_vlan200** — 설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인
4. **Agg1 / root_priority_vlan100_agg1** — STP priority 설정이 아직 반영 안 됐을 가능성 — 설정 재확인 및 재수렴 대기
5. **Agg1 / root_priority_vlan200_agg1** — STP priority 설정이 아직 반영 안 됐을 가능성 — 설정 재확인 및 재수렴 대기
