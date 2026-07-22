"""
comparator + scorer 파이프라인을 실제로 실행해 검증한다.

주의(중요): 이 스크립트의 "collected_vlan" / "collected_stp" 데이터는
샌드박스 환경 네트워크 제약상 LAB1 장비에 실제 SSH로 접속해 받아온 게 아니라,
D12 세션에서 문서로 이미 기록된 실제 상태
(VLAN 설계는 전부 적용 완료 / STP 우선순위는 아직 미적용 -> Access1,Access3 오선출)
를 그대로 재현한 값이다. show_vlan.py / show_spanning_tree.py 파서 자체는
위에서 각각 독립적으로 실제 텍스트를 파싱해 검증했다.

본인 노트북에서 engine/collector.py로 LAB1에 실제 접속해 받은 값을
collected_vlan / collected_stp 자리에 그대로 넣으면 이 데모와 동일하게 동작한다.
"""
import yaml
from engine.comparator import compare_vlan_stage, compare_stp_stage, build_vlan_index
from engine.scorer import score_all, print_scoreboard

with open("labs/lab1_campus/target_state.yaml") as f:
    target_state = yaml.safe_load(f)
with open("labs/lab1_campus/stages.yaml") as f:
    stages_cfg = yaml.safe_load(f)["stages"]

# --- VLAN 단계: D12 기준 이미 완료 상태로 기록됨 -> 전부 존재 ---
collected_vlan = {
    "Core1":   {1: {}, 100: {}, 200: {}, 999: {}},
    "Core2":   {1: {}, 100: {}, 200: {}, 999: {}},
    "Agg1":    {1: {}, 100: {}, 200: {}, 999: {}},
    "Agg2":    {1: {}, 100: {}, 200: {}, 999: {}},
    "Access1": {1: {}, 100: {}, 999: {}},
    "Access2": {1: {}, 100: {}, 999: {}},
    "Access3": {1: {}, 200: {}, 999: {}},
}

# --- STP 단계: D12 기록 상태 재현 (우선순위 미적용 -> 기본값 32768, root 오선출) ---
collected_stp = {
    "Core1":   {1: {"configured_priority": 32768, "is_root": False}},
    "Core2":   {1: {"configured_priority": 32768, "is_root": True}},   # MAC 우연으로 root
    "Agg1":    {100: {"configured_priority": 32768, "is_root": False},
                200: {"configured_priority": 32768, "is_root": False},
                999: {"configured_priority": 32768, "is_root": False}},
    "Agg2":    {100: {"configured_priority": 32768, "is_root": False},
                200: {"configured_priority": 32768, "is_root": False},
                999: {"configured_priority": 32768, "is_root": False}},
    "Access1": {100: {"configured_priority": 32768, "is_root": True}},   # 오선출
    "Access2": {100: {"configured_priority": 32768, "is_root": False}},
    "Access3": {200: {"configured_priority": 32768, "is_root": True}},   # 오선출
}

vlan_index = build_vlan_index(collected_stp)

stage_results = {
    "stage_vlan": compare_vlan_stage(target_state["stage_vlan"]["checks"], collected_vlan),
    "stage_stp": compare_stp_stage(target_state["stage_stp"]["checks"], collected_stp, vlan_index),
}

scored = score_all(stages_cfg, stage_results)
print_scoreboard(scored, session_label="(재현 데이터: D12 세션 기준 상태)")
