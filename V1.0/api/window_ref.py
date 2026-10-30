"""
pywebview의 window 객체는 __main__에서 webview.create_window() 호출 후에만 존재.
여러 API mixin 파일(discovery_api.py, inventory_api.py의 file dialog 등)이 이 객체가
필요한데, 모듈을 쪼개면서 전역변수를 직접 import하기보다 이 작은 참조 홀더를 통해 공유한다.
"""
_window = None


def set_window(window):
    global _window
    _window = window


def get_window():
    if _window is None:
        raise RuntimeError("window가 아직 설정되지 않음 — __main__에서 webview.create_window() 이후 set_window() 호출 필요")
    return _window
