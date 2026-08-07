"""CRTStreamWatcher — SecureCRT 세션 로그의 '새로 덧붙여진 부분만' 실시간으로 낚아채는 tail 스레드.

기존 api/log_analysis_run_api.py의 start_crt_log_watcher()는 mtime 디바운스로 '파일이 다 쓰였다'를
판단해 전체 파일을 00_orignal_log로 복사하는 용도다. 이쪽은 목적이 다르다 — 작업자가 명령을 치는
순간(0.3초 이내)의 차분 텍스트를 Diff 엔진에 흘려보내는 것이 전부이며, 파일을 옮기거나 전체를
재파싱하지 않는다.

핵심 규칙:
  * 파일당 바이트 오프셋을 기억하고 크기가 커졌을 때만 seek(offset) 후 read.
  * 줄 중간에서 읽기가 끊길 수 있으므로 마지막 미완성 줄은 버퍼에 남겨 다음 tick에 이어붙인다.
  * 파일이 줄어들면(세션 로그 재생성/rotate) 오프셋을 0으로 되돌린다.
  * **감시 시작 전부터 있던 파일**은 이후 tail을 EOF부터 하되, 시작 시점에 마지막
    seed_scan_bytes(기본 256KB)를 is_history=True로 한 번 넘긴다. 프로그램을 켜기 전부터
    SecureCRT가 열려 있던 경우가 흔해서, 그 세션에서 이미 벌어진 변경을 모르고 시작하면
    '지금 상태'가 틀리기 때문이다. 수신부는 history를 판정에는 쓰되 토스트는 띄우지 않는다.
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
# 프로그램을 켜기 전부터 SecureCRT가 열려 있던 경우, 그 세션에서 이미 벌어진 일을 놓치지 않도록
# 최신 로그의 끝부분을 이만큼 읽어 판정까지 돌린다(is_history=True로 넘어가므로 토스트는 안 뜬다).
# 파일 전체가 아니라 끝부분인 이유: 며칠짜리 세션 로그를 통째로 재판정하면 한참 전에 끝난
# 작업이 지금 경고로 되살아난다.
_SEED_SCAN_BYTES = 262144  # 256KB


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
                 extensions=DEFAULT_EXTENSIONS, seed_history=True, max_depth=1,
                 latest_only=True, seed_scan_bytes=_SEED_SCAN_BYTES):
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
        # 장비 1대당 '가장 최근에 기록된 로그 파일' 하나만 따라간다. SecureCRT는 접속 세션마다
        # 새 로그 파일을 만들기 때문에, 같은 장비의 지난 세션 파일까지 tail하면 이미 끝난
        # 작업의 로그가 지금 입력처럼 다시 판정된다.
        self.latest_only = latest_only
        # 감시를 시작하기 전부터 있던 파일에서 되짚어 읽을 바이트 수. 0이면 예전처럼 EOF부터.
        self.seed_scan_bytes = int(seed_scan_bytes or 0)

        self._offsets = {}      # {abs_path: 다음에 읽을 바이트 위치}
        # {device: 지금 tail 중인 파일} — SecureCRT를 껐다 켜면 장비의 로그 파일이 바뀐다.
        # 그 전환을 알아야 '이전 파일은 닫고 새 파일을 이어서 연다'를 상태로 보여줄 수 있다.
        self._device_path = {}
        self._rollovers = []    # [{device, old, new, ts}] — 진단 화면/로그용(최근 것만 유지)
        self._partial = {}      # {abs_path: 개행으로 끝나지 않은 잔여 문자열}
        self._devices = {}      # {abs_path: 확정된 장비명}
        self._unresolved = {}   # {abs_path: 식별 실패한 추정 이름} — 진단용
        # 지금 실제로 tail 중인 파일 경로. 감시 스레드가 매 tick 갱신하고, status()(=JS 브리지
        # 스레드)는 이 사본만 읽는다 — 브리지 쪽에서 파일 IO를 다시 하면 스레드 경합이 생긴다.
        self._active_paths = ()
        self._thread = None
        self._stop = threading.Event()
        self._first_tick_done = False

    # ---------- 라이프사이클 ----------
    def set_watch_dir(self, watch_dir):
        """감시 폴더를 바꾸고 파일 상태(오프셋/장비 판정)를 전부 비운다.

        **살아 있는 세션 로그 폴더만 넘겨야 한다.** 오프셋을 비우므로 새 폴더의 파일은
        '감시 시작 전부터 있던 것'으로 등록되어 끝부분이 재판정된다(seed). 점검 결과
        폴더를 넘기면 이미 끝난 점검의 출력이 지금 입력처럼 판정된다 — 실제로 그 버그가
        있었고(api/log_analysis_run_api.py의 refresh_realtime_baseline_after_inspection
        주석 참고), 지금은 _iter_log_files()가 점검 결과 파일을 이름으로 걸러 2차 방어한다.
        """
        self.watch_dir = str(watch_dir)
        self._offsets.clear()
        self._partial.clear()
        self._devices.clear()
        self._unresolved.clear()
        self._device_path.clear()
        self._rollovers = []
        self._active_paths = ()
        self._first_tick_done = False

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
        return iter_session_log_files(self.watch_dir, self.extensions, self.max_depth)

    def _select_active(self):
        """이번 tick에 tail할 파일 목록 — 장비별로 mtime이 가장 최신인 파일 1개씩.

        같은 장비의 지난 세션 로그를 계속 따라가면, 이미 끝난 작업의 입력이 지금 들어온 것처럼
        다시 판정되어 경고가 중복으로 쏟아진다.

        장비를 식별하지 못한 파일은 **따라가지 않는다**. CRTlog 폴더에는 이번 점검 대상이 아닌
        장비의 로그도 섞여 있고, 그것까지 붙들고 있으면 tracked_files가 부풀고 진단이 흐려진다.
        대신 매 tick 여기서 식별을 다시 시도하므로(_resolve_device), 접속 직후 프롬프트가 아직
        안 찍혀서 실패한 파일도 프롬프트가 찍히는 순간 감시 대상으로 올라온다."""
        if not self.latest_only:
            return list(self._iter_log_files())
        newest = {}      # {device: (mtime, path)}
        for path in self._iter_log_files():
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            device = self._devices.get(path) or self._resolve_device(path)
            if not device:
                continue   # 식별 실패 — 추적하지 않는다(다음 tick에 다시 시도)
            current = newest.get(device)
            if current is None or mtime > current[0]:
                newest[device] = (mtime, path)
        return [path for _mtime, path in newest.values()]

    def _tick(self):
        active = self._select_active()
        self._active_paths = tuple(active)
        self._note_rollovers(active)
        for path in active:
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

    def _note_rollovers(self, active):
        """장비가 따라가던 파일이 바뀌었으면(=SecureCRT를 껐다 켬) 기록해 둔다.

        전환 자체는 _select_active()가 알아서 한다(장비별 최신 파일 1개). 여기서는 '이전 파일을
        닫고 새 파일로 이어붙였다'는 사실만 남긴다 — 이전 파일의 오프셋은 지우지 않는다.
        지웠다가 그 파일이 다시 최신이 되면(수동 편집·시간 역전) 처음부터 재판정되어 이미 처리한
        경고가 통째로 다시 쏟아진다. 이전 파일에서 찾은 오류는 판정 쪽(RealtimeMonitor)이
        장비 단위로 들고 있으므로 파일이 바뀌어도 그대로 이어진다.
        """
        import time
        for path in active:
            device = self._devices.get(path)
            if not device:
                continue
            previous = self._device_path.get(device)
            if previous == path:
                continue
            self._device_path[device] = path
            if previous:
                self._rollovers.append({
                    "device": device, "old": os.path.basename(previous),
                    "new": os.path.basename(path), "ts": time.time(),
                })
                del self._rollovers[:-20]

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
        # 프로그램을 켜기 전부터 SecureCRT가 열려 있던 경우가 흔하다. 그 세션에서 이미 벌어진
        # 변경(설정 삭제, 링크 DOWN)을 못 보고 시작하면 '지금 상태'가 틀리게 된다 — 최신 로그의
        # 끝부분을 되짚어 한 번 넘긴다. is_history=True이므로 수신부는 토스트를 띄우지 않는다.
        seed_bytes = max(self.seed_scan_bytes, _SEED_TAIL_BYTES) if self.seed_history else 0
        if seed_bytes and size > 0:
            start = max(0, size - seed_bytes)
            seed = self._read_range(path, start, size)
            if seed:
                # 잘린 첫 줄은 버린다(중간부터 시작한 쓰레기 줄)
                if start > 0 and "\n" in seed:
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
        active = self._active_paths
        return {
            "running": self.is_running(),
            "watch_dir": self.watch_dir,
            "interval": self.interval,
            "extensions": list(self.extensions),
            "latest_only": self.latest_only,
            # tracked_files는 '지금 따라가는 파일 수'다 — 예전에는 한 번이라도 오프셋을 잡은
            # 파일 전부를 셌기 때문에, 장비별 최신 파일만 tail하도록 바뀐 뒤로는 실제보다
            # 큰 수가 나온다(지난 세션 파일까지 포함).
            "tracked_files": len(active),
            # 진단 화면이 '어느 파일이 추적 중인지'를 표시할 수 있게 절대경로를 그대로 넘긴다.
            "active_paths": list(active),
            "matched": {os.path.basename(p): d for p, d in self._devices.items()},
            # 장비별로 지금 어느 파일을 열고 있는지 + 세션 재접속으로 파일이 바뀐 이력.
            "device_files": {d: os.path.basename(p) for d, p in self._device_path.items()},
            "rollovers": list(self._rollovers),
            # 장비를 못 찾은 파일 — '감시가 안 된다'의 원인이 대개 이것이라 화면에 노출한다.
            "unmatched": sorted(self._unresolved.values()),
        }


def iter_session_log_files(root, extensions=DEFAULT_EXTENSIONS, max_depth=1):
    """감시 대상이 되는 '살아 있는 세션 로그' 파일 경로를 훑는다.

    감시 스레드(_iter_log_files)와 진단 화면(api의 probe_realtime_log_files)이 **같은 함수**를
    써야 한다. 예전에는 진단 쪽만 최상위 폴더를 listdir했기 때문에, 하위 폴더로 로깅하는
    환경에서 '감시는 추적 중인데 진단 목록에는 안 보이는' 화면이 나왔다 — 진단이 감시를
    설명하지 못하면 진단이 아니다.

    두 가지를 제외한다:
      * 대상 확장자(.txt/.log)가 아닌 파일.
      * 점검 SSH 세션이 남긴 결과 파일({stamp}_raw_{device}.txt). 그건 지나간 한 시점의
        스냅샷이고 여기서 다루는 것은 '지금 들어오는 입력'이다. 섞이면 이미 끝난 점검의
        출력이 방금 친 명령처럼 재판정된다(`show reload cause` 출력 헤더 'Reload Cause:'가
        CRITICAL '위험 명령 실행'으로 잡힌 실제 사고). 감시 폴더를 잘못 지정하는 어떤 경로
        에도 걸리도록 디렉터리가 아니라 **파일 이름**으로 막는다.
    """
    from engine.baseline_store import is_inspection_log
    root = str(root)
    suffixes = tuple(e.lower() for e in extensions)
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # depth: root=0, 바로 아래 폴더=1. 예전 식은 relpath 의 구분자 개수만 셌는데
        # 'session1' 에는 구분자가 없어서 1단계 하위가 0으로 계산됐고, 결과적으로
        # max_depth=1 인데 2단계까지 내려갔다(문서와 동작이 어긋난 채로 더 많이 훑었다).
        depth = 0 if dirpath == root else os.path.relpath(dirpath, root).count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if name.lower().endswith(suffixes) and not is_inspection_log(name):
                yield os.path.join(dirpath, name)


def _default_device_resolver(path, head_text=""):
    from engine.baseline_store import device_from_filename
    return device_from_filename(path)
