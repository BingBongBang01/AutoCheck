"""
SSH 접속 준비 로직의 단일 출처 — 개인키 로딩과 paramiko connect 인자 조립.

원래 api/terminal_session_api.py 안에만 있던 것을 engine 계층으로 내렸다.
지금은 두 곳이 같은 규칙(비밀번호 / 개인키 파일 / 붙여넣은 키 본문)으로 접속해야 한다:
  - api/terminal_session_api.py  (대화형 터미널 세션)
  - engine/device_probe.py       (장비 목록의 자동 연결 확인)
접속 방식이 갈라지면 "터미널은 되는데 연결 확인은 실패" 같은 어긋남이 생기므로 여기 모아둔다.
"""
import io

# paramiko 는 import 만으로 약 340 ms 가 든다(이전 측정(Windows)). api/terminal_session_api.py
# 가 이 모듈을 모듈 레벨로 import 하므로, 최상단에서 paramiko 를 끌어오면 SSH 를 한 번도
# 쓰지 않는 실행에서도 그 비용을 낸다. 실제 접속 시점까지 미룬다.
#
# 주의: 여기서 얻는 것은 '앱 시작이 빨라진다'뿐이고, 첫 접속에서 그 비용을 그대로 낸다.
# 첫 접속은 이미 네트워크 대기가 있는 동작이라 340 ms 가 묻힌다.
_KEY_CLASS_NAMES = ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey")
_key_classes_cache = None


def _paramiko():
    """paramiko 모듈을 필요할 때 가져온다. 미설치면 여기서 ImportError 가 난다 —
    예전에는 모듈 import 시점에 났으므로 오류가 나는 위치만 바뀐다."""
    import paramiko

    return paramiko


def _key_classes():
    """시도할 개인키 클래스들 — 처음 호출할 때 한 번만 결정하고 캐시한다.

    DSSKey 는 paramiko 4.x 에서 제거됐다(DSA 자체가 폐기됨) — 이름으로 찾아서 있는 것만 쓴다.
    하드코딩하면 최신 paramiko 가 깔린 PC 에서 AttributeError 로 키 접속이 통째로 죽는다.
    """
    global _key_classes_cache
    if _key_classes_cache is None:
        paramiko = _paramiko()
        _key_classes_cache = tuple(
            cls for cls in (getattr(paramiko, name, None) for name in _KEY_CLASS_NAMES) if cls
        )
    return _key_classes_cache


def load_private_key(path, passphrase=None):
    last_err = None
    for key_cls in _key_classes():
        try:
            return key_cls.from_private_key_file(path, password=passphrase)
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"키 파일을 읽을 수 없습니다({path}): {last_err}")


def load_private_key_text(text, passphrase=None):
    """화면에 붙여넣은 키 본문을 메모리에서 읽는다.

    예전엔 %TEMP% 에 평문으로 쓴 뒤 읽고 지웠다 — 지우기 전에 앱이 죽거나 백신·백업·동기화 도구가
    그 사이에 집어 가면 평문 개인키가 디스크에 남는다. 오류 문장에 키 본문은 넣지 않는다."""
    last_err = None
    for key_cls in _key_classes():
        try:
            return key_cls.from_private_key(io.StringIO(text), password=passphrase)
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"붙여넣은 개인키를 읽을 수 없습니다(형식·암호 확인): {type(last_err).__name__}")


def build_connect_kwargs(target, timeout=10):
    """
    target: {ip, port, username, password, auth_method, key_path, key_content, key_passphrase}
    paramiko SSHClient.connect()에 그대로 넘길 kwargs를 만든다.

    key_content(UI에 붙여넣은 키 본문)만 있고 파일 경로가 없으면 메모리에서 바로 읽는다
    (load_private_key_text — 디스크에 쓰지 않는다).
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
    passphrase = target.get("key_passphrase") or None
    if key_path:
        kwargs["pkey"] = load_private_key(key_path, passphrase)
    else:
        kwargs["pkey"] = load_private_key_text(target["key_content"], passphrase)
    return kwargs


def connect(target, timeout=10):
    """접속된 paramiko.SSHClient를 반환. 실패 시 paramiko/OS 예외를 그대로 올린다.

    호스트키는 target 의 host_key_scope(고객사 단위 — engine/host_keys)에서 이 장비의 기억한 키로 확인한다. 처음 보는
    장비면 받은 키를 기억하고, 다르면 비밀번호를 보내기 전에 끊고 host_keys.HostKeyChanged 를 낸다 — 예전엔
    AutoAddPolicy 로 아무 키나 받았다(IMPROVEMENT_PLAN 4-6)."""
    from engine import host_keys

    paramiko = _paramiko()
    kwargs = build_connect_kwargs(target, timeout=timeout)
    scope = target.get("host_key_scope") or host_keys.GLOBAL_SCOPE
    device = target.get("name", "")
    client = paramiko.SSHClient()
    host_keys.prepare_client(client, scope, kwargs["hostname"], kwargs["port"], device)
    try:
        client.connect(**kwargs)
    except Exception as exc:
        client.close()
        changed = host_keys.from_exception(exc, scope, kwargs["hostname"], kwargs["port"], device)
        if changed is not None and changed is not exc:
            raise changed from exc
        raise
    return client
