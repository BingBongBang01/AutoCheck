"""FileAlarmHandler — Critical Finding을 history/{project}/alarms.log 에 누적 기록."""
import os
import json
import datetime

try:
    from alarm.base import AlarmHandler, register
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from alarm.base import AlarmHandler, register


class FileAlarmHandler(AlarmHandler):
    handler_name = "file"

    def __init__(self, base_dir="history"):
        self.base_dir = base_dir

    def notify(self, project_id, findings):
        if not findings:
            return
        lab_dir = os.path.join(self.base_dir, project_id)
        os.makedirs(lab_dir, exist_ok=True)
        log_path = os.path.join(lab_dir, "alarms.log")
        ts = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            for finding in findings:
                entry = {"notified_at": ts, "project_id": project_id, **finding}
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


register(FileAlarmHandler())
