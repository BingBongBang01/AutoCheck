"""
채점 결과를 세션(회차)별로 history/{lab_name}/{timestamp}.json 에 저장한다.
"""
import os
import json
import glob
import datetime


def save_history(lab_name, scored, elapsed_sec, base_dir="history"):
    session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    lab_dir = os.path.join(base_dir, lab_name)
    os.makedirs(lab_dir, exist_ok=True)

    path = os.path.join(lab_dir, f"{session_ts}.json")
    payload = {
        "session": session_ts,
        "elapsed_sec": round(elapsed_sec, 2),
        "stages": scored,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_latest(lab_name, base_dir="history"):
    lab_dir = os.path.join(base_dir, lab_name)
    files = sorted(glob.glob(os.path.join(lab_dir, "*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def load_previous(lab_name, base_dir="history"):
    """가장 최근 것 말고 그 전 회차 (diff 비교용)."""
    lab_dir = os.path.join(base_dir, lab_name)
    files = sorted(glob.glob(os.path.join(lab_dir, "*.json")))
    if len(files) < 2:
        return None
    with open(files[-2], encoding="utf-8") as f:
        return json.load(f)
