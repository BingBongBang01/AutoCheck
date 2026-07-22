"""
config_snapshots/{device}_{date}.txt 두 개(전회/이번회)를 라인 단위로 비교.
"""
import difflib


def diff_configs(old_text, new_text):
    """반환: {"added": [...], "removed": [...], "changed": bool}"""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]

    return {
        "added": added,
        "removed": removed,
        "changed": bool(added or removed),
    }


def find_latest_two_snapshots(snapshot_dir, device_name):
    """config_snapshots/{lab}/ 안에서 특정 장비의 최근 2개 스냅샷 경로를 (이전, 최신) 순으로 리턴."""
    import os, glob
    pattern = os.path.join(snapshot_dir, f"{device_name}_*.txt")
    files = sorted(glob.glob(pattern))
    if len(files) < 2:
        return None, None
    return files[-2], files[-1]


if __name__ == "__main__":
    old = "hostname Core1\ninterface Eth1\n  no shutdown\nvlan 100\n  name USER\n"
    new = "hostname Core1\ninterface Eth1\n  no shutdown\n  description uplink\nvlan 100\n  name USER\nvlan 200\n  name SERVER\n"
    result = diff_configs(old, new)
    print("추가된 라인:", result["added"])
    print("제거된 라인:", result["removed"])
    print("변경 여부:", result["changed"])
