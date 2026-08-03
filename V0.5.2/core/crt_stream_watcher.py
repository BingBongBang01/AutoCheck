"""CRTStreamWatcher — SecureCRT 세션 로그의 '새로 덧붙여진 부분만' 실시간으로 낚아채는 tail 스레드.

기존 api/log_analysis_run_api.py의 start_crt_log_watcher()는 mtime 디바운스로 '파일이 다 쓰였다'를
판단해 전체 파일을 00_orignal_log로 복사하는 용도다. 이쪽은 목적이 다르다 — 작업자가 터미널에서
명령을 치는 순간(0.3초 이내)의 차분 텍스트를 Diff 엔진에 흘려보내는 것이 전부이며, 파일을 옮기거나
전체를 재파싱하지 않는다.

핵심 규칙:
  * 파일당 바이트 오프셋을 기억하고 크기가 커졌을 때만 seek(offset) 후 read.
  * 줄 중간에서 읽기가 끊길 수 있으므로 마지막 미완성 줄은 버퍼에 남겨 다음 tick에 이어붙인다.
  * 파일이 줄어들면(세션 로그 재생성/rotate) 오프셋을 0으로 되돌린다.
  * 감시를 시작한 시점의 기존 내용은 '이미 지난 일'이므로 EOF부터 시작한다(catch_up=False 기본).
"""
import os
import threading
import time


class CRTStreamWatcher:
    """*.txt tail 스레드. 새 텍스트가 생기면 on_delta(device_name, text, path)를 호출한다.

    on_delta는 워커 스레드에서 호출되므로 예외를 던져도 감시가 죽지 않도록 내부에서 잡는다.
    """

    def __init__(self, watch_dir, on_delta, interval=0.3, device_resolver=None,
                 catch_up=False, on_error=None, encoding="utf-8"):
        self.watch_dir = str(watch_dir)
        self.on_delta = on_delta
        self.interval = interval
        self.device_resolver = device_resolver or _default_device_resolver
        self.catch_up = catch_up
        self.on_error = on_error
        self.encoding = encoding

        self._offsets = {}     # {abs_path: 다음에 읽을 바이트 위치}
        self._partial = {}     # {abs_path: 개행으로 끝나지 않은 잔여 문자열}
        self._thread = None
        self._stop = threading.Event()

    # ---------- 라이프사이클 ----------
    def start(self):
        if self.is_running():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CRTStreamWatcher")
        self._thread.start()
        return True

    def stop(self, join_timeout=2.0):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=join_timeout)
        self._thread = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---------- 내부 루프 ----------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # 감시 스레드는 어떤 이유로도 죽지 않아야 한다
                self._report(exc)
            self._stop.wait(self.interval)

    def _tick(self):
        if not os.path.isdir(self.watch_dir):
            return
        try:
            names = os.listdir(self.watch_dir)
        except OSError:
            return

        for name in names:
            if not name.lower().endswith(".txt"):
                continue
            path = os.path.join(self.watch_dir, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue

            known = self._offsets.get(path)
            if known is None:
                # 처음 본 파일: 기본은 EOF부터(과거 내용은 실시간 이벤트가 아니다)
                self._offsets[path] = 0 if self.catch_up else size
                self._partial[path] = ""
                if not self.catch_up:
                    continue
                known = 0

            if size < known:
                # rotate / 재생성 — 처음부터 다시 읽는다
                known = 0
                self._partial[path] = ""

            if size == known:
                continue

            delta = self._read_delta(path, known, size)
            if delta:
                self._dispatch(path, delta)

    def _read_delta(self, path, start, size):
        """start~size 구간을 읽어 '완성된 줄들'만 문자열로 반환. 오프셋도 함께 전진시킨다."""
        try:
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(size - start)
        except OSError:
            return ""
        self._offsets[path] = start + len(chunk)
        if not chunk:
            return ""

        text = self._partial.get(path, "") + chunk.decode(self.encoding, errors="replace")
        # 마지막 줄이 개행으로 끝나지 않았다면 아직 타이핑/출력 중 — 다음 tick으로 넘긴다
        if text.endswith("\n") or text.endswith("\r"):
            self._partial[path] = ""
            return text
        cut = max(text.rfind("\n"), text.rfind("\r"))
        if cut < 0:
            self._partial[path] = text
            return ""
        self._partial[path] = text[cut + 1:]
        return text[:cut + 1]

    def _dispatch(self, path, delta):
        try:
            device = self.device_resolver(path)
        except Exception:
            device = os.path.basename(path)
        try:
            self.on_delta(device, delta, path)
        except Exception as exc:
            self._report(exc)

    def _report(self, exc):
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:
                pass

    # ---------- 상태 ----------
    def status(self):
        return {
            "running": self.is_running(),
            "watch_dir": self.watch_dir,
            "interval": self.interval,
            "tracked_files": len(self._offsets),
        }


def _default_device_resolver(path):
    from engine.baseline_store import device_from_filename
    return device_from_filename(path)
