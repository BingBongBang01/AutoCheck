"""MaskingApiMixin — 'Log Masking' 탭 담당. 최신 점검 회차(runs/<run_id>)의 raw(전체 원본)
또는 problem(필터링된 이상탐지 결과) 중 하나를 소스로 골라, 선택한 항목만
format-preserving 마스킹해 같은 회차의 masked/에 저장한다.

실행 전에 한 파일로 미리 보고(preview_log_masking), 실행 뒤에는 결과에 남은 것(비밀·고르지 않은 항목·장비 이름)을
알린다 — IMPROVEMENT_PLAN 4-10 의 마스킹 미리보기와 누출 검사."""
import glob
import os

_SOURCES = ("original", "problem")


class MaskingApiMixin:
    def get_mask_options(self):
        """'Log Masking' 탭 체크리스트 — [{"key","label"}, ...] (engine/log_masking.MASK_KEY_ORDER 순서)."""
        from engine import log_masking
        return log_masking.get_mask_options()

    def _masking_source(self, source, run_id=None):
        """(프로파일 경로, 소스 폴더의 .txt 목록) 또는 {"error"}. run_id: 마스킹할 점검 회차(화면에서 고른 것, 없으면 최신)."""
        if source not in _SOURCES:
            return {"error": "잘못된 소스입니다."}
        # 결과를 쓸 masked/ 폴더가 필요하므로 create=True. 점검 이력이 없으면 마스킹할 원본도 없다.
        profile_paths = self._active_profile_log_paths(create=True, run_id=run_id)
        if not profile_paths:
            from api.log_analysis_run_api import NO_RUN_ERROR
            return {"error": NO_RUN_ERROR if run_id else "활성 프로파일이 없습니다."}
        files = sorted(glob.glob(os.path.join(profile_paths[source], "*.txt")))
        if not files:
            return {"error": "마스킹할 로그가 없습니다. 먼저 점검(또는 로그 분석)을 실행하세요."}
        return profile_paths, files

    def _masking_device_names(self):
        """누출 검사에 쓸 활성 프로파일 장비 목록의 이름(장비명·호스트명). 읽지 못하면 빈 목록."""
        try:
            devices = self._load_inventory(self._paths())["devices"]
        except Exception:
            return ()
        return tuple(str(d[key]) for d in devices for key in ("name", "hostname") if d.get(key))

    def preview_log_masking(self, source, categories, filename=None, run_id=None):
        """실행 전에 한 파일(filename — 없으면 첫 파일)을 마스킹해 본다. 파일은 쓰지 않는다.
        반환: {ok, file, files, lines, changed_lines, counts, sample, leaks} — 고르지 않아도 된다(그러면 무엇이
        그대로 나가는지 보인다)."""
        from core.text_io import read_log_text
        from engine import log_masking
        found = self._masking_source(source, run_id)
        if isinstance(found, dict):
            return found
        _paths, files = found
        names = [os.path.basename(f) for f in files]
        path = files[names.index(filename)] if filename in names else files[0]
        result = log_masking.preview_masking(read_log_text(path), set(categories or ()), self._masking_device_names())
        return {"ok": True, "file": os.path.basename(path), "files": names, **result}

    def run_log_masking(self, source, categories, run_id=None):
        """source: 'original'(raw 전체) | 'problem'(problem 필터링 결과).
        categories: get_mask_options()의 key 중 사용자가 선택한 것들.
        반환에 leaks(결과 파일 전체에 남은 것 — 규칙별 합계와 장비 이름)를 싣는다."""
        if source not in _SOURCES:
            return {"error": "잘못된 소스입니다."}
        if not categories:
            return {"error": "마스킹할 항목을 하나 이상 선택하세요."}
        from engine import log_masking
        found = self._masking_source(source, run_id)
        if isinstance(found, dict):
            return found
        profile_paths, _files = found
        results = log_masking.run_masking(profile_paths[source], profile_paths["masking"], set(categories),
                                          self._masking_device_names())
        # 마스킹 결과는 외부 공유용이다 — 비밀값이 남았으면 공유하기 전에 알아야 한다.
        unmasked = {}
        for result in results:
            for key, count in result["unmasked_secrets"].items():
                unmasked[key] = unmasked.get(key, 0) + count
        return {"ok": True, "results": results, "leaks": _total_leaks(results),
                "warning": log_masking.describe_unmasked(unmasked)}


def _total_leaks(results):
    """파일마다의 leak_report 를 합친다 — {rules: [{key,label,count,secret,selected}], names: [{name,count}], secrets, missed}."""
    rules, names = {}, {}
    for result in results:
        leaks = result.get("leaks") or {}
        for row in leaks.get("rules") or []:
            entry = rules.setdefault(row["key"], {k: row[k] for k in ("key", "label", "secret", "selected")} | {"count": 0})
            entry["count"] += row["count"]
        for row in leaks.get("names") or []:
            names[row["name"]] = names.get(row["name"], 0) + row["count"]
    rows = list(rules.values())
    return {"rules": rows, "names": [{"name": n, "count": c} for n, c in sorted(names.items(), key=lambda x: x[0].lower())],
            "secrets": sum(r["count"] for r in rows if r["secret"]),
            "missed": sum(r["count"] for r in rows if r["selected"] and not r["secret"])}
