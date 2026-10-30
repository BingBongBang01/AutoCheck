"""HostKeyApiMixin — 장비 목록의 'SSH 호스트키'(IMPROVEMENT_PLAN 4-6, engine/host_keys.py).

활성 프로파일의 고객사 범위에서 기억한 키와 바뀐 키를 보여 주고, 바뀐 키를 '다시 신뢰'하거나 기억한 키를 지운다.
화면에는 키 본문 대신 지문만 보낸다(대조는 지문으로 한다).
"""


class HostKeyApiMixin:
    def _host_key_scope(self):
        from engine import host_keys
        return host_keys.scope_for_project(self._project())

    def get_host_keys(self):
        """{"trusted": [{host, port, key_type, fingerprint, first_seen, device}], "changed": [... seen_at, trusted_fingerprint]}"""
        from engine import host_keys
        try:
            scope = self._host_key_scope()
        except RuntimeError:
            return {"trusted": [], "changed": []}
        return host_keys.listing(scope)

    def trust_host_key(self, host, port):
        """바뀐 키를 다시 신뢰한다 — 사용자가 지문을 확인하고 누른 것이다."""
        from engine import host_keys
        try:
            scope = self._host_key_scope()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        if not host_keys.trust_changed(scope, host, port):
            return {"error": "다시 신뢰할 바뀐 키가 없습니다 — 연결 확인을 다시 해 보세요."}
        return {"ok": True}

    def forget_host_key(self, host, port):
        """기억한 키를 지운다 — 다음 접속이 그때 받은 키를 새로 기억한다."""
        from engine import host_keys
        try:
            scope = self._host_key_scope()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        if not host_keys.forget(scope, host, port):
            return {"error": "지울 키가 없습니다."}
        return {"ok": True}
