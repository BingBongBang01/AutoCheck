"""DashboardApiMixin — Health Score/KPI + 로그 수집 커버리지/정상·비정상 집계.

집계 원본은 활성 프로파일의 점검 로그(data/<고객사>/<프로파일>/runs/<run_id>/raw)에서 뽑은
장비별 '최신' 로그다(Reports/Findings 탭과 동일한 소스). 점검 이력이 없으면 모든 수치가 0이 된다 —
예전에는 labs/{project}/terminal_sessions/까지 훑어서, 프로파일에 데이터가 없어도 대시보드에
과거 랩의 통계가 떠 있었다. 원본 로그를 매 렌더마다 전수 스캔하면 장비/로그가 늘어날수록 느려지므로,
스캔 결과는 (프로젝트, 최신 로그 mtime, 파일 수)를 키로 TTL 캐시에 담아 재사용한다.
"""
import os
import time
import datetime
from collections import Counter

from engine import project_manager as pm

# 심각도는 더 이상 대시보드가 키워드로 직접 판정하지 않는다 — config/log_rules.json의
# keyword_severity/signatures를 읽는 규칙 엔진이 판정하고, 여기서는 표시용으로 접기만 한다.
# 규칙 엔진은 4단계(critical/major/minor/info)로 판정하지만 대시보드 배지는 2단계뿐이라
# 접어서 보여준다 — 서비스에 실제 영향이 있는 두 등급만 critical로 올린다.
_DASHBOARD_SEVERITY = {"critical": "critical", "major": "critical",
                       "minor": "warning", "info": "warning"}


def _dashboard_severity(engine_severity):
    return _DASHBOARD_SEVERITY.get(engine_severity, "warning")

# 요약 지표는 초 단위 실시간성이 필요 없다 — 동일 결과 재계산을 막는 캐시 TTL(초).
_SCAN_CACHE_TTL = 30.0
# 수집 지연(최신 로그가 이 시간보다 오래됐으면 대시보드 수치를 '과거 통계'로 표시) 임계값(초).
STALE_AFTER_SEC = 24 * 3600

_scan_cache = {"key": None, "at": 0.0, "value": None}


def _humanize_lag(seconds):
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}초 전"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    if seconds < 86400:
        return f"{int(seconds // 3600)}시간 전"
    return f"{int(seconds // 86400)}일 전"


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


class DashboardApiMixin:
    def _run_count(self, project_id):
        """KPI '세션' 수 = 실제로 저장된 점검 회차(runs/<run_id>) 개수.
        예전에는 상대경로 history/{project}/*.json을 세서(CWD 의존) exe 실행 시 항상 0이었고,
        로그 파일 개수를 세던 시절에는 장비 수만큼 부풀려진 값이 나왔다."""
        if not project_id:
            return 0
        from engine import log_storage
        customer, profile = self._resolve_active_customer_profile_names()
        return len(log_storage.list_run_dirs(customer, profile))

    def _scan_latest_logs(self, project_id):
        """장비별 최신 점검 로그를 한 번만 훑어 대시보드가 필요한 모든 집계를 만든다.
        
        데이터 소스는 engine/log_storage.latest_original_logs() 한 곳뿐이다 — 대시보드가
        자기만의 스캔 규칙을 따로 들고 있으면 보고서·Findings 탭과 수치가 어긋난다.
        반환: {devices: {device: {...}}, total_lines, abnormal_lines, keyword_counts, latest_mtime}
        """
        from engine import log_storage
        from engine.log_cache import cached_findings, cached_log_text

        paths_by_device = (log_storage.latest_original_logs(*log_storage.resolve_names_from_project(project_id))
                           if project_id else {})

        key = (project_id,
               max((m for m, _ in paths_by_device.values()), default=0),
               len(paths_by_device))
        now = time.time()
        if _scan_cache["key"] == key and now - _scan_cache["at"] < _SCAN_CACHE_TTL:
            return _scan_cache["value"]

        devices = {}
        total_lines = abnormal_lines = 0
        keyword_counts = Counter()
        keyword_severity = {}
        for device, (mtime, path) in paths_by_device.items():
            # 같은 로그를 보고서/Findings/AI 분석도 파싱한다 — 파일이 바뀌지 않았으면 공유한다.
            text = cached_log_text(path)
            lines = text.splitlines()
            findings = cached_findings(path, text=text)
            hit_count = sum(f.get("repeat", 1) for f in findings)
            crit = sum(f.get("repeat", 1) for f in findings
                       if _dashboard_severity(f.get("severity")) == "critical")
            per_keyword = Counter()
            for f in findings:
                per_keyword[f["keyword"].upper()] += f.get("repeat", 1)
                keyword_severity[f["keyword"].upper()] = _dashboard_severity(f.get("severity"))
            devices[device] = {
                "device": device,
                "incidents": [{"keyword": kw, "count": c,
                               "severity": keyword_severity.get(kw, "warning")}
                              for kw, c in per_keyword.most_common()],
                "collected_at": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "mtime": mtime,
                "lines": len(lines),
                "abnormal": hit_count,
                "critical": crit,
                "warning": hit_count - crit,
                "size": os.path.getsize(path) if os.path.exists(path) else 0,
            }
            total_lines += len(lines)
            abnormal_lines += hit_count
            keyword_counts.update(per_keyword)

        value = {
            "devices": devices,
            "total_lines": total_lines,
            "abnormal_lines": abnormal_lines,
            "keyword_counts": keyword_counts,
            "keyword_severity": keyword_severity,
            "latest_mtime": max((d["mtime"] for d in devices.values()), default=None),
        }
        _scan_cache.update({"key": key, "at": now, "value": value})
        return value

    def get_dashboard(self):
        project_id = pm.get_active_project()

        # --- 1) 맥락: 고객사 / 프로파일 -------------------------------------------------
        if project_id:
            customer_name, profile_name = self._resolve_active_customer_profile_names()
        else:
            customer_name, profile_name = "-", "-"

        # --- 2) 장비 수집 커버리지 -----------------------------------------------------
        from engine import device_inventory as di
        paths = pm.project_paths(project_id) if project_id else None
        inv = self._load_inventory(paths) if project_id else {"devices": [], "defaults": {}}
        total_devices = len(inv["devices"])
        enabled_devices = di.get_enabled_devices(inv)
        enabled_names = [d["name"] for d in enabled_devices]

        scan = self._scan_latest_logs(project_id) if project_id else {
            "devices": {}, "total_lines": 0, "abnormal_lines": 0,
            "keyword_counts": Counter(), "latest_mtime": None}
        log_devices = scan["devices"]

        # 인벤토리에 등록됐고 로그도 만들어진 장비 = 수집 성공. 등록됐지만 로그가 없으면 미수집
        # (접속 실패/미실행). 인벤토리에 없는데 로그만 있는 장비는 태깅 누락으로 따로 표시한다.
        collected_names = [n for n in enabled_names if n in log_devices]
        missing_names = [n for n in enabled_names if n not in log_devices]
        untagged_names = sorted(n for n in log_devices if n not in enabled_names)
        enabled_count = len(enabled_names)
        coverage = {
            "total": total_devices,
            "enabled": enabled_count,
            "collected": len(collected_names),
            "collected_pct": _pct(len(collected_names), enabled_count),
            "missing": len(missing_names),
            "missing_pct": _pct(len(missing_names), enabled_count),
            "missing_devices": missing_names,
            "untagged_devices": untagged_names,
        }

        # --- 3) 정상 / 비정상 로그 ------------------------------------------------------
        total_lines = scan["total_lines"]
        abnormal_lines = scan["abnormal_lines"]
        logs = {
            "total_lines": total_lines,
            "normal_lines": total_lines - abnormal_lines,
            "abnormal_lines": abnormal_lines,
            "normal_pct": _pct(total_lines - abnormal_lines, total_lines),
            "abnormal_pct": _pct(abnormal_lines, total_lines),
        }

        # --- 4) 수집 지연(Ingestion Lag) — 모든 수치의 신뢰도를 보증하는 선행 지표 -------
        latest_mtime = scan["latest_mtime"]
        lag_seconds = (time.time() - latest_mtime) if latest_mtime else None
        freshness = {
            "latest_at": datetime.datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M:%S")
                         if latest_mtime else None,
            "lag_seconds": round(lag_seconds) if lag_seconds is not None else None,
            "lag_text": _humanize_lag(lag_seconds),
            "stale": bool(lag_seconds is not None and lag_seconds > STALE_AFTER_SEC),
        }

        # --- 5) 에러 유형 Top N / 비정상 상위 장비 ---------------------------------------
        error_types = [{"keyword": kw, "count": c,
                        "severity": scan["keyword_severity"].get(kw, "warning"),
                        "pct": _pct(c, abnormal_lines)}
                       for kw, c in scan["keyword_counts"].most_common(10)]
        top_hosts = sorted((d for d in log_devices.values() if d["abnormal"]),
                           key=lambda d: (-d["critical"], -d["abnormal"], d["device"]))[:10]
        top_hosts = [{k: v for k, v in d.items() if k != "mtime"} for d in top_hosts]

        # --- 6) Health / 요약 ---------------------------------------------------------------
        # 중복 제거된 인시던트 수 = (장비, 키워드) 조합 수. 원시 줄 수(abnormal_lines)와 함께 보여줘야
        # "50,000줄 = 50,000개 문제"라는 오독을 막을 수 있다.
        incident_list = [i for d in log_devices.values() for i in d["incidents"]]
        incidents = {
            "total": len(incident_list),
            "critical": sum(1 for i in incident_list if i["severity"] == "critical"),
            "warning": sum(1 for i in incident_list if i["severity"] == "warning"),
            "raw_lines": abnormal_lines,
        }

        base = {"context": {"customer": customer_name, "profile": profile_name,
                            "project_id": project_id},
                "coverage": coverage, "logs": logs, "freshness": freshness,
                "error_types": error_types, "top_hosts": top_hosts,
                "incidents": incidents}

        # Health 는 "100점에서 감점" 방식을 인시던트 단위로 적용한다 — 줄 수로
        # 감점하면 같은 장애가 반복 출력된 장비 하나 때문에 전체가 0점이 돼 변별력이 사라진다.
        # (채점 파이프라인 이력으로 계산하던 분기는 D1 퇴역과 함께 없앴다 — 점검 로그가 유일한 근거다.)
        critical = sum(d["critical"] for d in log_devices.values())
        warning = sum(d["warning"] for d in log_devices.values())
        inspected = len(log_devices)
        log_scores = {}
        for device, d in log_devices.items():
            penalty = sum(15 if i["severity"] == "critical" else 5 for i in d["incidents"])
            log_scores[device] = max(0, 100 - penalty)
        health = round(sum(log_scores.values()) / len(log_scores)) if log_scores else 0
        sessions_count = self._run_count(project_id)
        summary = (f"점검 로그 기반 — {inspected}대 장비 점검, 인시던트 {incidents['total']}건"
                   f"(Critical {incidents['critical']}/Warning {incidents['warning']}), "
                   f"이상 징후 줄 {abnormal_lines:,}건."
                   if inspected else "아직 점검 로그가 없습니다.")
        base.update({
            "kpi": {"health": health, "critical": critical, "warning": warning,
                    "total_devices": total_devices, "reachable": None, "offline": None,
                    "running": len(enabled_devices), "sessions": sessions_count},
            "health_basis": "점검 로그 인시던트 감점(Critical -15/Warning -5)",
            "ai_summary": summary, "device_scores": log_scores})
        return base
