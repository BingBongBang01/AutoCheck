"""NotificationApiMixin — 환경 설정의 '알림 채널' 카드(IMPROVEMENT_PLAN 4-4).

채널(Teams·Slack·웹훅·메일)과 전체 옵션(사용·가림 수준·중복 억제·시간당 한도·보낼 사건)을 읽고 고친다. 웹훅 주소와
SMTP 비밀번호는 화면으로 보내지 않는다 — 저장 여부(has_*)와 웹훅 호스트만 보낸다(engine/notifications.public_settings).
화면이 보낸 비밀 칸은 새로 입력한 것만 바뀌고, clear_secrets 로 지운다(장비 목록 4-1 과 같은 규칙).
"""


class NotificationApiMixin:
    def get_notification_settings(self):
        """알림 설정(비밀값 없이) + 채널별 마지막 전송 상태."""
        from engine import notifications
        return notifications.public_settings()

    def save_notification_settings(self, options):
        """전체 옵션 저장 — {enabled, mask, dedupe_minutes, max_per_hour, events:{…}}. 채널은 건드리지 않는다."""
        from engine import notifications
        settings = notifications.load_settings()
        try:
            notifications.apply_options(settings, options)
        except notifications.NotificationError as exc:
            return {"error": str(exc)}
        notifications.save_settings(settings)
        return {"ok": True, **notifications.public_settings(settings)}

    def save_notification_channel(self, fields):
        """채널 추가(id 없음)·수정(id) — webhook_url/password 는 새로 넣은 것만 바뀐다."""
        from engine import notifications
        settings = notifications.load_settings()
        try:
            channel = notifications.apply_channel(settings, fields)
        except notifications.NotificationError as exc:
            return {"error": str(exc)}
        notifications.save_settings(settings)
        return {"ok": True, "channel_id": channel["id"], **notifications.public_settings(settings)}

    def remove_notification_channel(self, channel_id):
        from engine import notifications
        settings = notifications.load_settings()
        if not notifications.remove_channel(settings, channel_id):
            return {"error": "없는 알림 채널입니다."}
        notifications.save_settings(settings)
        return {"ok": True, **notifications.public_settings(settings)}

    def test_notification_channel(self, channel_id):
        """'시험 보내기' — 저장된 채널로 시험 메시지를 바로 보낸다(실제로 밖으로 나간다)."""
        from engine import notifications
        settings = notifications.load_settings()
        channel = next((c for c in settings["channels"] if c["id"] == channel_id), None)
        if channel is None:
            return {"error": "없는 알림 채널입니다."}
        if channel.get("_sealed"):
            return {"ok": False, "detail": "이 PC 에서 풀 수 없는 비밀값이 있습니다 — 다시 입력해 저장하세요."}
        error = notifications.notifier.test(channel)
        if error:
            return {"ok": False, "detail": f"보내지 못했습니다: {error}"}
        return {"ok": True, "detail": "보냈습니다 — 받는 쪽에서 확인하세요."}
