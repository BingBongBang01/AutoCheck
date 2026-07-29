"""DashboardApiMixin — Health Score/KPI + 로그 수집 커버리지/정상·비정상 집계.

집계 원본은 labs/{project}/terminal_sessions/의 장비별 '최신' 점검 로그다(Reports/Findings 탭과
동일한 소스). 원본 로그를 매 렌더마다 전수 스캔하면 장비/로그가 늘어날수록 대시보드가 멈추므로,
스캔 결과는 (프로젝트, 최신 로그 mtime, 파일 수)를 키로 TTL 캐시에 담아 재사용한다.
"""
import os
import time
import datetime
from collections import Counter

from engine import project_manager as pm

# 이상 징후 키워드를 심각도 2단계로 나눈다 — 서비스 중단/실패를 직접 뜻하는 키워드는 Critical,
# 성능 열화·오류 카운터 성격은 Warning. config/log_rules.json에 키워드가 추가되면 기본은 Warning.
CRITICAL_KEYWORDS = {"FAIL", "ERROR", "CRITICAL", "DOWN", "TIMEOUT", "UNREACHABLE"}

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


def _scan_latest_logs(project_id):
    """장비별 최신 점검 로그를 한 번만 훑어 대시보드가 필요한 모든 집계를 만든다.

    반환: {devices: {device: {...}}, total_lines, abnormal_lines, keyword_counts, latest_mtime}
    """
    from api.report_api import _latest_terminal_log_paths_by_device
    from api.log_file_browser_api import _read_text_auto
    from engine.log_analysis import classify_line

    paths_by_device = _latest_terminal_log_paths_by_device(project_id)
    key = (project_id,
           max((m for m, _ in paths_by_device.values()), default=0),
           len(paths_by_device))
    now = time.time()
    if _scan_cache["key"] == key and now - _scan_cache["at"] < _SCAN_CACHE_TTL:
        return _scan_cache["value"]

    devices = {}
    total_lines = abnormal_lines = 0
    keyword_counts = Counter()
    for device, (mtime, path) in paths_by_device.items():
        text = _read_text_auto(path)
        lines = text.splitlines()
        hits = [kw for kw in (classify_line(line) for line in lines) if kw]
        crit = sum(1 for kw in hits if kw.upper() in CRITICAL_KEYWORDS)
        # 같은 원인이 수백 줄 반복되는 걸 그대로 세면 알람 피로도만 키운다 — (장비, 키워드)를
        # 하나의 '인시던트'로 묶고 반복 횟수는 속성으로만 남긴다.
        per_keyword = Counter(kw.upper() for kw in hits)
        devices[device] = {
            "device": device,
            "incidents": [{"keyword": kw, "count": c,
                           "severity": "critical" if kw in CRITICAL_KEYWORDS else "warning"}
                          for kw, c in per_keyword.most_common()],
            "collected_at": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime": mtime,
            "lines": len(lines),
            "abnormal": len(hits),
            "critical": crit,
            "warning": len(hits) - crit,
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
        }
        total_lines += len(lines)
        abnormal_lines += len(hits)
        keyword_counts.update(kw.upper() for kw in hits)

    value = {
        "devices": devices,
        "total_lines": total_lines,
        "abnormal_lines": abnormal_lines,
        "keyword_counts": keyword_counts,
        "latest_mtime": max((d["mtime"] for d in devices.values()), default=None),
    }
    _scan_cache.update({"key": key, "at": now, "value": value})
    return value


class DashboardApiMixin:
    def get_dashboard(self):
        project_id = pm.get_active_project()

        # --- 1) 맥락: 고객사 / 프로파일 -------------------------------------------------
        if project_id:
            customer_name, profile_name = self.resolve_active_customer_profile_names()
        else:
            customer_name, profile_name = "-", "-"

        # --- 2) 장비 수집 커버리지 -----------------------------------------------------
        from engine import device_inventory as di
        paths = pm.project_paths(project_id) if project_id else None
        inv = self._load_inventory(paths) if project_id else {"devices": [], "defaults": {}}
        total_devices = len(inv["devices"])
        enabled_devices = di.get_enabled_devices(inv)
        enabled_names = [d["name"] for d in enabled_devices]

        scan = _scan_latest_logs(project_id) if project_id else {
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
                        "severity": "critical" if kw in CRITICAL_KEYWORDS else "warning",
                        "pct": _pct(c, abnormal_lines)}
                       for kw, c in scan["keyword_counts"].most_common(10)]
        top_hosts = sorted((d for d in log_devices.values() if d["abnormal"]),
                           key=lambda d: (-d["critical"], -d["abnormal"], d["device"]))[:10]
        top_hosts = [{k: v for k, v in d.items() if k != "mtime"} for d in top_hosts]

        # --- 6) Health / Stage / AI 요약 -------------------------------------------------
        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None

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

        if not latest:
            # 채점 이력이 없어도 점검 로그만으로 KPI를 채운다. Health는 core.health_score와 같은
            # "100점에서 감점" 방식을 인시던트 단위로 적용한다 — 줄 수로 감점하면 같은 장애가
            # 반복 출력된 장비 하나 때문에 전체가 0점이 돼 변별력이 사라진다.
            critical = sum(d["critical"] for d in log_devices.values())
            warning = sum(d["warning"] for d in log_devices.values())
            inspected = len(log_devices)
            log_scores = {}
            for device, d in log_devices.items():
                penalty = sum(15 if i["severity"] == "critical" else 5 for i in d["incidents"])
                log_scores[device] = max(0, 100 - penalty)
            health = round(sum(log_scores.values()) / len(log_scores)) if log_scores else 0
            sessions_count = len(self.list_log_files()) if project_id else 0
            summary = (f"점검 로그 기반 — {inspected}대 장비 점검, 인시던트 {incidents['total']}건"
                       f"(Critical {incidents['critical']}/Warning {incidents['warning']}), "
                       f"이상 징후 줄 {abnormal_lines:,}건."
                       if inspected else "아직 채점 이력/점검 로그가 없습니다.")
            base.update({
                "kpi": {"health": health, "critical": critical, "warning": warning,
                        "total_devices": total_devices, "reachable": None, "offline": None,
                        "running": len(enabled_devices), "sessions": sessions_count},
                "health_basis": "점검 로그 인시던트 감점(Critical -15/Warning -5)",
                "stages": [], "ai_summary": summary, "device_scores": log_scores})
            return base

        stages = latest["stages"]
        findings = latest.get("findings", [])

        from core.health_score import score_project
        if findings:
            health_result = score_project(findings)
            health = round(health_result["project_score"])
            # "(network-wide)"는 실제 장비가 아니라 STP root 교차검증용 가짜 device — 표시에서 제외
            device_scores = {k: v for k, v in health_result["device_scores"].items() if k != "(network-wide)"}
            health_basis = "채점 이력(Rule 위반 감점)"
        else:
            # v0.0.10 이전 세션(findings 없음) — 예전 방식(PASS 비율)으로 하위호환 폴백
            total_pass = sum(s["pass"] for s in stages)
            total_all = sum(s["total"] for s in stages)
            health = round(100 * total_pass / total_all) if total_all else 0
            device_scores = {}
            health_basis = "채점 이력(PASS 비율)"

        from ai_analysis.rule_based import analyze
        ai_result = analyze(stages)
        critical = sum(1 for a in ai_result["all_anomalies"] if a["result"] == "FAIL")
        warning = sum(1 for a in ai_result["all_anomalies"] if a["result"] == "UNKNOWN")

        import glob
        sessions_count = len(glob.glob(f"history/{project_id}/*.json")) if project_id else 0

        base.update({
            "kpi": {"health": health, "critical": critical, "warning": warning,
                    "total_devices": total_devices, "reachable": None, "offline": None,
                    "running": len(enabled_devices), "sessions": sessions_count},
            "health_basis": health_basis,
            "stages": [{"label": s["label"], "pass": s["pass"], "total": s["total"], "status": s["status"]} for s in stages],
            "ai_summary": ai_result["summary"],
            "device_scores": device_scores,
        })
        return base
