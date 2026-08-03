"""CRTStreamWatcher — SecureCRT 세션 로그의 '새로 덧붙여진 부분만' 실시간으로 낚아채는 tail 스레드.

기존 api/log_analysis_run_api.py의 start_crt_log_watcher()는 mtime 디바운스로 '파일이 다 쓰였다'를
판단해 전체 파일을 00_orignal_log로 복사하는 용도다. 이쪽은 목적이 다르다 — 작업자가 명령을 치는
순간(0.3초 이내)의 차분 텍스트를 Diff 엔진에 흘려보내는 것이 전부이며, 파일을 옮기거나 전체를
재파싱하지 않는다.

핵심 규칙:
  * 파일당 바이트 오프셋을 기억하고 크기가 커졌을 때만 seek(offset) 후 read.
  * 줄 중간에서 읽기가 끊길 수 있으므로 마지막 미완성 줄은 버퍼에 남겨 다음 tick에 이어붙인다.
  * 파일이 줄어들면(세션 로그 재생성/rotate) 오프셋을 0으로 되돌린다.
  * **감시 시작 전부터 있던 파일**은 EOF부터 읽는다(과거 작업이 지금 경고로 쏟아지면 안 된다).
    단 화면이 비어 보이지 않게 마지막 몇 KB를 is_history=True로 한 번 넘겨준다.
  * **감시 시작 후 새로 생긴 파일**은 처음부터 읽는다. 이건 지금 진행 중인 작업이고, 파일 생성과
    첫 tick 사이(≤0.3초)에 이미 로그인 배너와 첫 명령이 기록돼 있기 때문이다. 예전에는 새 파일도
    EOF부터 시작해서, 프로그램을 켠 뒤 SecureCRT를 열면 초기 명령이 통째로 유실됐다.
  * SecureCRT 기본 세션 로그 이름은 session.log다 — .txt만 보면 그 파일을 통째로 놓친다.
"""
import os
import threading

DEFAULT_EXTENSIONS = (".txt", ".log")
_HEAD_BYTES = 4096      # 장비 식별용으로 파일 앞부분에서 읽는 양
_SEED_TAIL_BYTES = 8192  # 기존 파일의 '최근 상황'을 화면에 채워줄 양


class CRTStreamWatcher:
    """세션 로그 tail 스레드. 새 텍스트가 생기면
    on_delta(device_name, text, path, is_history=False)를 호출한다.

    device_resolver(path, head_text) -> 장비명 또는 None.
      None을 반환하면 그 파일은 이번 tick에서 건너뛴다(다음 tick에 다시 판정 —
      로그인 배너만 있고 아직 프롬프트가 안 찍혀서 식별에 실패한 경우 곧 성공한다).
    on_delta는 워커 스레드에서 호출되므로 예외를 던져도 감시가 죽지 않도록 내부에서 잡는다.
    """

    def __init__(self, watch_dir, on_delta, interval=0.3, device_resolver=None,
                 catch_up=False, on_error=None, encoding="utf-8",
                 extensions=DEFAULT_EXTENSIONS, seed_history=True, max_depth=1):
        self.watch_dir = str(watch_dir)
        self.on_delta = on_delta
        self.interval = interval
        self.device_resolver = device_resolver or _default_device_resolver
        self.catch_up = catch_up
        self.on_error = on_error
        self.encoding = encoding
        self.extensions = tuple(e.lower() for e in extensions)
        self.seed_history = seed_history
        self.max_depth = max_depth

        self._offsets = {}      # {abs_path: 다음에 읽을 바이트 위치}
        self._partial = {}      # {abs_path: 개행으로 끝나지 않은 잔여 문자열}
        self._devices = {}      # {abs_path: 확정된 장비명}
        self._unresolved = {}   # {abs_path: 식별 실패한 추정 이름} — 진단용
        self._thread = None
        self._stop = threading.Event()
        self._first_tick_done = False

    # ---------- 라이프사이클 ----------
    def start(self):
        if self.is_running():
            return False
        self._stop.clear()
        self._first_tick_done = False
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
                self._first_tick_done = True
            except Exception as exc:  # 감시 스레드는 어떤 이유로도 죽지 않아야 한다
                self._report(exc)
            self._stop.wait(self.interval)

    def _iter_log_files(self):
        """감시 폴더와 (max_depth까지의) 하위 폴더에서 대상 확장자 파일 경로를 훑는다.
        SecureCRT를 세션별 하위 폴더로 로깅하도록 설정한 환경도 있어서 1단계는 내려간다."""
        root = self.watch_dir
        if not os.path.isdir(root):
            return
        for dirpath, dirnames, filenames in os.walk(root):
            depth = os.path.relpath(dirpath, root).count(os.sep) if dirpath != root else 0
            if depth >= self.max_depth:
                dirnames[:] = []
            for name in filenames:
                if name.lower().endswith(self.extensions):
                    yield os.path.join(dirpath, name)

    def _tick(self):
        for path in self._iter_log_files():
            try:
                size = os.path.getsize(path)
            except OSError:
                continue

            known = self._offsets.get(path)
            if known is None:
                self._register_file(path, size)
                known = self._offsets[path]
                if known >= size:
                    continue

            if size < known:
                # rotate / 재생성 — 처음부터 다시 읽는다
                known = 0
                self._partial[path] = ""

            if size == known:
                continue

            delta = self._read_delta(path, known, size)
            if delta:
                self._dispatch(path, delta, is_history=False)

    def _register_file(self, path, size):
        """처음 본 파일의 시작 오프셋 결정 + 장비 식별 + (기존 파일이면) 화면 시딩."""
        self._partial[path] = ""
        # 첫 tick에 존재한 파일 = 감시 시작 전부터 있던 것. 그 뒤에 나타난 파일 = 지금 열린 세션.
        pre_existing = not self._first_tick_done
        if self.catch_up or not pre_existing:
            self._offsets[path] = 0
            self._resolve_device(path)
            return

        self._offsets[path] = size
        # EOF부터 읽으면 파일 내용을 한 줄도 안 보게 되므로, 식별에 쓸 앞부분은 따로 읽어 둔다.
        self._resolve_device(path)
        if self.seed_history and size > 0:
            seed = self._read_range(path, max(0, size - _SEED_TAIL_BYTES), size)
            if seed:
                # 잘린 첫 줄은 버린다(중간부터 시작한 쓰레기 줄)
                if size > _SEED_TAIL_BYTES and "\n" in seed:
                    seed = seed.split("\n", 1)[1]
                self._dispatch(path, seed, is_history=True)

    # ---------- 장비 식별 ----------
    def _resolve_device(self, path):
        head = self._read_range(path, 0, _HEAD_BYTES)
        try:
            device = self.device_resolver(path, head)
        except Exception as exc:
            self._report(exc)
            device = None
        if device:
            self._devices[path] = device
            self._unresolved.pop(path, None)
        else:
            self._unresolved[path] = os.path.basename(path)
        return device

    # ---------- 읽기 ----------
    def _read_range(self, path, start, end):
        try:
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(max(0, end - start))
        except OSError:
            return ""
        return chunk.decode(self.encoding, errors="replace")

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

    def _dispatch(self, path, delta, is_history):
        device = self._devices.get(path)
        if not device:
            # 아직 식별 못 한 파일 — 방금 열려서 프롬프트가 안 찍혔을 수 있으므로 매번 다시 시도한다.
            device = self._resolve_device(path)
        if not device:
            return
        try:
            self.on_delta(device, delta, path, is_history)
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
            "extensions": list(self.extensions),
            "tracked_files": len(self._offsets),
            "matched": {os.path.basename(p): d for p, d in self._devices.items()},
            # 장비를 못 찾은 파일 — '감시가 안 된다'의 원인이 대개 이것이라 화면에 노출한다.
            "unmatched": sorted(self._unresolved.values()),
        }


def _default_device_resolver(path, head_text=""):
    from engine.baseline_store import device_from_filename
    return device_from_filename(path)
