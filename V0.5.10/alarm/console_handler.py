"""ConsoleAlarmHandler — 기본 콘솔 즉시 출력 핸들러."""
try:
    from alarm.base import AlarmHandler, register
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from alarm.base import AlarmHandler, register


class ConsoleAlarmHandler(AlarmHandler):
    handler_name = "console"

    def notify(self, project_id, findings):
        if not findings:
            return
        print(f"\n[ALARM] {project_id} — Critical Finding {len(findings)}건 발생")
        for f in findings:
            print(f"  !! {f.get('device')} / {f.get('check_id')}: {f.get('result')} (evidence={f.get('evidence')})")


register(ConsoleAlarmHandler())
