"""비밀값 금고 — 장비 비밀번호·개인키·passphrase, 클라우드 AI API 키를 디스크에 평문으로 두지 않는다(IMPROVEMENT_PLAN 4-1).

Windows DPAPI(CryptProtectData, 현재 사용자 범위)로 암호화해 원래 자리에 'vault:dpapi:v1:<base64>' 문자열로 둔다.
파일 구조는 그대로라 YAML/JSON 을 읽는 코드는 바뀌지 않고, 값을 디스크에 쓰는 곳에서 seal, 읽어 쓰는 곳에서 reveal 한다.

  - DPAPI 는 이 PC 의 이 Windows 사용자만 풀 수 있다. 다른 PC·다른 사용자로 옮긴 파일의 비밀값은 풀리지 않는다 —
    reveal 이 VaultError 를 낸다(호출부는 '다시 입력하세요'로 안내한다). 그래서 다른 PC 로 옮기는 내보내기는
    비밀값을 빼거나, 사용자가 '계정 정보 포함'을 고르면 풀어서 평문으로 담는다.
  - 평문 값은 reveal 이 그대로 돌려준다 — 이전(seal) 전 파일도 그대로 읽힌다. 다음 저장 때 봉인된다.
  - Windows 가 아니면(개발용) DPAPI 가 없다 — seal 은 값을 그대로 두고 한 번 경고한다(available() 이 False).
"""
import base64
import sys

__all__ = ["VaultError", "available", "is_sealed", "seal", "reveal", "seal_fields", "reveal_fields"]

PREFIX = "vault:dpapi:v1:"
# 앱 전용 보조 엔트로피 — 같은 사용자의 다른 프로그램이 DPAPI 로 이 값을 '그냥' 풀지 못하게 한 겹 더한다.
_ENTROPY = b"AutoCheck.secret_vault.v1"
_DESCRIPTION = "AutoCheck"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class VaultError(Exception):
    """봉인된 값을 풀 수 없을 때(다른 PC·사용자에서 봉인됐거나 값이 깨짐) 또는 봉인에 실패했을 때."""


_dpapi = None
_warned = False


def _load_dpapi():
    """ctypes 로 crypt32 의 CryptProtectData/CryptUnprotectData 를 묶는다(Windows 전용, 새 의존성 없음)."""
    global _dpapi
    if _dpapi is not None or sys.platform != "win32":
        return _dpapi
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_p = ctypes.POINTER(DataBlob)
    crypt32.CryptProtectData.argtypes = [blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, blob_p]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [blob_p, ctypes.POINTER(wintypes.LPWSTR), blob_p, ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, blob_p]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    def blob(data):
        buffer = ctypes.create_string_buffer(data, len(data))
        return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def call(function, data, *middle):
        data_in, keep_in = blob(data)
        entropy, keep_entropy = blob(_ENTROPY)
        out = DataBlob()
        if not function(ctypes.byref(data_in), *middle[:1], ctypes.byref(entropy), None, None,
                        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise VaultError(f"DPAPI 오류 {ctypes.get_last_error()}")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    _dpapi = {
        "protect": lambda data: call(crypt32.CryptProtectData, data, _DESCRIPTION),
        "unprotect": lambda data: call(crypt32.CryptUnprotectData, data, None),
    }
    return _dpapi


def available() -> bool:
    """이 환경에서 봉인할 수 있는가(Windows DPAPI)."""
    return _load_dpapi() is not None


def is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value):
    """평문 -> 봉인 문자열. 빈 값·None·이미 봉인된 값은 그대로 돌려준다."""
    global _warned
    if not isinstance(value, str) or value == "" or is_sealed(value):
        return value
    dpapi = _load_dpapi()
    if dpapi is None:
        if not _warned:
            _warned = True
            print("[금고] 이 환경에는 Windows DPAPI 가 없어 비밀값을 평문으로 둡니다(개발용).")
        return value
    return PREFIX + base64.b64encode(dpapi["protect"](value.encode("utf-8"))).decode("ascii")


def reveal(value):
    """봉인 문자열 -> 평문. 평문·빈 값은 그대로. 풀 수 없으면 VaultError."""
    if not is_sealed(value):
        return value
    dpapi = _load_dpapi()
    if dpapi is None:
        raise VaultError("이 환경에는 Windows DPAPI 가 없어 봉인된 값을 풀 수 없습니다.")
    try:
        raw = base64.b64decode(value[len(PREFIX):].encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise VaultError("봉인된 값이 깨졌습니다.") from exc
    try:
        return dpapi["unprotect"](raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VaultError("봉인된 값이 깨졌습니다.") from exc


def seal_fields(data, names):
    """dict/list 를 따라 내려가며 키 이름이 names 에 있는 문자열 값을 봉인한 사본과, 새로 봉인한 개수.
    원본은 바꾸지 않는다."""
    if isinstance(data, dict):
        out, count = {}, 0
        for key, value in data.items():
            if key in names and isinstance(value, str) and value and not is_sealed(value):
                out[key] = seal(value)
                count += out[key] != value
            else:
                out[key], inner = seal_fields(value, names)
                count += inner
        return out, count
    if isinstance(data, list):
        out, count = [], 0
        for item in data:
            sealed, inner = seal_fields(item, names)
            out.append(sealed)
            count += inner
        return out, count
    return data, 0


def reveal_fields(data, names):
    """seal_fields 의 반대 — 풀린 사본과, 풀지 못해 빈 값으로 둔 키 이름 목록."""
    if isinstance(data, dict):
        out, failed = {}, []
        for key, value in data.items():
            if key in names and is_sealed(value):
                try:
                    out[key] = reveal(value)
                except VaultError:
                    out[key] = ""
                    failed.append(key)
            else:
                out[key], inner = reveal_fields(value, names)
                failed.extend(inner)
        return out, failed
    if isinstance(data, list):
        out, failed = [], []
        for item in data:
            revealed, inner = reveal_fields(item, names)
            out.append(revealed)
            failed.extend(inner)
        return out, failed
    return data, []
