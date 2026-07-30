"""
SSH 접속 준비 로직의 단일 출처 — 개인키 로딩과 paramiko connect 인자 조립.

원래 api/terminal_session_api.py 안에만 있던 것을 engine 계층으로 내렸다.
지금은 두 곳이 같은 규칙(비밀번호 / 개인키 파일 / 붙여넣은 키 본문)으로 접속해야 한다:
  - api/terminal_session_api.py  (대화형 터미널 세션)
  - engine/device_probe.py       (장비 목록의 자동 연결 확인)
접속 방식이 갈라지면 "터미널은 되는데 연결 확인은 실패" 같은 어긋남이 생기므로 여기 모아둔다.
"""
import os
import tempfile

import paramiko

# 키 종류를 파일 내용만 보고 알 수 없으므로 흔한 순서대로 시도한다.
# DSSKey는 paramiko 4.x에서 제거됐다(DSA 자체가 폐기됨) — 이름으로 찾아서 있는 것만 쓴다.
# 하드코딩하면 최신 paramiko가 깔린 PC에서 AttributeError로 키 접속이 통째로 죽는다.
_KEY_CLASS_NAMES = ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey")
_KEY_CLASSES = tuple(cls for cls in (getattr(paramiko, n, None) for n in _KEY_CLASS_NAMES) if cls)


def load_private_key(path, passphrase=None):
    last_err = None
    for key_cls in _KEY_CLASSES:
        try:
            return key_cls.from_private_key_file(path, password=passphrase)
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"키 파일을 읽을 수 없습니다({path}): {last_err}")


def build_connect_kwargs(target, timeout=10):
    """
    target: {ip, port, username, password, auth_method, key_path, key_content, key_passphrase}
    paramiko SSHClient.connect()에 그대로 넘길 kwargs를 만든다.

    key_content(UI에 붙여넣은 키 본문)만 있고 파일 경로가 없으면 임시 파일에 써서 읽은 뒤
    즉시 지운다 — pkey 객체는 이미 메모리에 올라와 있으므로 파일이 사라져도 접속에 문제없다.
    """
    kwargs = dict(
        hostname=target["ip"], port=int(target.get("port") or 22),
        username=target.get("username", ""), timeout=timeout,
        banner_timeout=timeout, auth_timeout=timeout,
        look_for_keys=False, allow_agent=False,
    )

    uses_key = target.get("auth_method") == "public_key" and (target.get("key_path") or target.get("key_content"))
    if not uses_key:
        kwargs["password"] = target.get("password", "")
        return kwargs

    key_path = target.get("key_path")
    temp_path = None
    if not key_path:
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False, encoding="utf-8")
        handle.write(target["key_content"])
        handle.close()
        key_path = temp_path = handle.name
    try:
        kwargs["pkey"] = load_private_key(key_path, target.get("key_passphrase") or None)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    return kwargs


def connect(target, timeout=10):
    """접속된 paramiko.SSHClient를 반환. 실패 시 paramiko/OS 예외를 그대로 올린다."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(**build_connect_kwargs(target, timeout=timeout))
    return client
