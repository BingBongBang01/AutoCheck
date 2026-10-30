"""프로세스 사이 잠금 — 같은 프로파일을 두 무인 정기점검이 동시에 돌리지 않게(IMPROVEMENT_PLAN 4-3).

작업 스케줄러가 띄운 프로세스와 화면의 '지금 실행'이 같은 프로파일을 동시에 점검하면 같은 장비에 SSH 세션이
겹친다. 또 앱을 여는 순간 '중단된 run 정리'(run_manager.recover_all_incomplete_runs)가 다른 프로세스가 아직
돌리고 있는 run 을 '중단'으로 바꿔 버린다. 그래서 프로파일마다 잠금 파일 하나를 두고 OS 파일 잠금(Windows
msvcrt.locking, 그 밖 fcntl.flock)으로 잡는다 — 프로세스가 죽으면 OS 가 풀어 주므로 PID 를 따지거나 오래된
잠금 파일을 치울 일이 없다. 같은 프로세스 안에서도 핸들이 다르면 서로 막는다.
"""
import hashlib
import os

from core.paths import AppPaths

try:
    import msvcrt
except ImportError:          # Windows 가 아닌 곳(개발·테스트)
    msvcrt = None
    import fcntl


def profile_lock_path(profile_dir):
    """프로파일 폴더 -> 그 프로파일의 잠금 파일. 프로파일 폴더 안에 두지 않는다 — 잠긴 바이트는 다른 프로세스가
    읽을 수 없어서, 점검 중에 워크스페이스를 zip 으로 내보내면 그 파일에서 실패한다."""
    key = os.path.normcase(os.path.abspath(str(profile_dir)))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return AppPaths.user_data_root() / "locks" / f"profile_{digest}.lock"


class FileLock:
    """기다리지 않는 배타 잠금. `with FileLock(p) as lock:` 뒤 lock.held 로 잡았는지 본다."""

    def __init__(self, path):
        self.path = str(path)
        self._fd = None

    @property
    def held(self):
        return self._fd is not None

    def acquire(self):
        """잡으면 True, 다른 프로세스(또는 이 프로세스의 다른 핸들)가 잡고 있으면 False."""
        if self._fd is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self):
        if self._fd is None:
            return
        try:
            if msvcrt is not None:
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def is_locked(path):
    """누가 잡고 있는지만 본다(잡아 보고 바로 놓는다). 잠금 파일이 없으면 False — 파일을 만들지 않는다."""
    if not os.path.exists(str(path)):
        return False
    probe = FileLock(path)
    if probe.acquire():
        probe.release()
        return False
    return True
