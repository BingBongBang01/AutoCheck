"""
History Manager — 완료된 Job(=Run) 하나의 산출물(CLI 원본 로그, 리포트, config
스냅샷, 그 Job에 해당하는 구조화 로그)을 고객사/프로파일/Job 단위로 보관한다.

engine/run_manager.py가 이미 runs/<run_id>/{raw,masked,parsed,analysis,reports,exports}
전체를 하나의 폴더로 관리하고, storage_service.archive_run()이 그 폴더 전체를
archive/<run_id>/로 옮겨준다 — 그래서 raw 로그/리포트/config 스냅샷의 "보관" 자체는
이미 되어 있다. 이 모듈이 새로 추가하는 것은 두 가지뿐이다:
  1) run 폴더 밖(logs/structured_<date>.jsonl)에 흩어져 있던, 이 Job과 상관관계(job_id)가
     맞는 구조화 로그 이벤트를 archive 전에 run 폴더 안으로 복사해 같은 보관 단위로 묶는다.
  2) 활성(runs/) + 보관(archive/) 목록을 하나로 합쳐 "이 프로파일의 전체 이력"으로 조회한다.
"""
import json

from core.paths import AppPaths
from core.storage_service import storage_service
from core.event_bus import bus as event_bus
from engine.profile_manager import profile_manager
from engine.run_manager import run_manager


class HistoryManager:
    def _collect_structured_events(self, job_id):
        """logs/structured_*.jsonl 전체에서 이 job_id와 일치하는 이벤트만 뽑아온다.
        날짜가 걸쳐 있을 수 있어(자정 근처 실행) 파일 하나만 보지 않고 전부 훑는다."""
        events = []
        log_dir = AppPaths.logs_root()
        if not log_dir.is_dir():
            return events
        for path in sorted(log_dir.glob("structured_*.jsonl")):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("job_id") == job_id:
                            events.append(record)
            except OSError:
                continue
        return events

    def archive_completed_run(self, customer, profile_name, run_id, job_id=None):
        """완료/실패/중단된 run 하나를 archive/로 옮긴다. job_id를 주면 그 Job에 해당하는
        구조화 로그 이벤트도 run 폴더 안(logs/structured_events.jsonl)에 같이 남긴다."""
        run, _session, _metadata = run_manager.load_run(customer, profile_name, run_id)

        if job_id:
            events = self._collect_structured_events(job_id)
            if events:
                text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
                storage_service.save_text(run, "logs/structured_events.jsonl", text, overwrite=True)

        dst = run_manager.archive_run(customer, profile_name, run_id)
        event_bus.publish("history.archived", {
            "customer": customer, "profile": profile_name, "run_id": run_id,
            "job_id": job_id, "path": str(dst),
        })
        return dst

    def list_history(self, customer, profile_name):
        """활성 runs(runs/)와 보관된 실행(archive/)을 최신순으로 합쳐서 돌려준다."""
        active = run_manager.list_runs(customer, profile_name)
        active_ids = {s.get("run_id") for s in active}

        archived = []
        profile = profile_manager.get_profile(customer, profile_name)
        archive_dir = profile.path / "archive"
        if archive_dir.is_dir():
            for entry in archive_dir.iterdir():
                if not entry.is_dir() or entry.name in active_ids:
                    continue
                session_path = entry / "session.json"
                if not session_path.exists():
                    continue
                try:
                    with open(session_path, encoding="utf-8") as f:
                        session = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                session["archived"] = True
                archived.append(session)

        combined = [dict(s, archived=s.get("archived", False)) for s in active] + archived
        combined.sort(key=lambda s: s.get("run_id", ""), reverse=True)
        return combined


history_manager = HistoryManager()
