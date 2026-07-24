"""LogAnalysisRunApiMixin — 'Log Analysis' 탭의 규칙기반/AI 분석 실행.
00_orignal_log -> 01_problem_log 파이프라인이며, 파일 목록/열람은 log_file_browser_api.py 참고.
이 클래스는 LogFileBrowserApiMixin(_active_profile_log_paths)과 SettingsApiMixin
(get_local_ai_config/ensure_lemonade_model_loaded/_load_ai_config)이 이미 조합된
Api 인스턴스 위에서만 동작한다.
"""
import os
import glob

from api.log_file_browser_api import _read_text_auto


class LogAnalysisRunApiMixin:
    def run_log_analysis(self):
        """'Log Analysis' 탭 — 00_orignal_log를 FSM으로 분석해 01_problem_log에 저장.
        반환: {"ok": True, "results": [{"source","problem_count","output"}, ...]}."""
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        from engine import log_analysis
        results = log_analysis.run_analysis(profile_paths["original"], profile_paths["problem"])
        return {"ok": True, "results": results}

    _AI_MODE_PREFIXES = {"local": "LocalAI_", "cloud": "CloudAI_"}

    def run_ai_log_analysis(self, ai_mode):
        """'Log Analysis' 탭 — 'Run Local AI Analysis' / 'Run Cloud AI Analysis' 버튼.
        00_orignal_log의 각 .txt를 설정된 AI(local NPU 또는 cloud API)에게 그대로 분석시켜
        01_problem_log에 "{LocalAI_|CloudAI_}{원본파일명}_problems.txt"로 저장 — 분석 방식별로
        접두어가 달라 규칙기반(RuleCheck_) 결과나 다른 AI 분석 결과를 덮어쓰지 않는다.
        반환: {"ok": True, "results": [{"source","output"}, ...]} 또는 {"error": ...}."""
        if ai_mode not in self._AI_MODE_PREFIXES:
            return {"error": f"알 수 없는 AI 모드: {ai_mode}"}
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}

        print(f"[AI 분석] 시작 mode={ai_mode}")

        if ai_mode == "local":
            api_cfg = self.get_local_ai_config()
            endpoint = api_cfg.get("endpoint")
            model = api_cfg.get("model")
            print(f"[AI 분석] 로컬 모델 준비 중: endpoint={endpoint} model={model}")
            ready = self.ensure_lemonade_model_loaded(endpoint, model)
            if not ready.get("ok"):
                print(f"[AI 분석] 로컬 모델 준비 실패: {ready.get('detail', '')}")
                return {"error": f"로컬 AI 모델 준비 실패: {ready.get('detail', '')}"}
            print(f"[AI 분석] 로컬 모델 준비 완료: {ready.get('detail', '')}")
        else:
            local_cfg = self._load_ai_config()
            node = next((p for p in local_cfg.get("providers", []) if p.get("type") == "cloud_apis"), None)
            entry = next((e for e in (node or {}).get("entries", []) if e.get("enabled") and e.get("api_key")), None)
            if entry is None:
                print("[AI 분석] 사용 가능한(체크되고 키가 등록된) 클라우드 API가 없음")
                return {"error": "Cloud AI 설정이 없습니다. 설정 탭에서 API 키를 등록하고 체크하세요."}
            api_cfg = entry
            print(f"[AI 분석] 클라우드 API 사용: provider={entry.get('provider')} name={entry.get('name')}")

        from ai_analysis.router import analyze_raw_log_text

        original_dir = profile_paths["original"]
        problem_dir = profile_paths["problem"]
        if not original_dir or not os.path.isdir(original_dir):
            return {"error": "00_orignal_log 폴더가 없습니다."}

        results = []
        os.makedirs(problem_dir, exist_ok=True)
        for path in sorted(glob.glob(os.path.join(original_dir, "*.txt"))):
            raw_text = _read_text_auto(path)
            analysis_text = analyze_raw_log_text(raw_text, ai_mode, api_cfg)
            if analysis_text.startswith("[AI 분석 오류]"):
                print(f"[AI 분석] 실패: {os.path.basename(path)} -> {analysis_text}")
            prefix = self._AI_MODE_PREFIXES[ai_mode]
            out_name = prefix + os.path.splitext(os.path.basename(path))[0] + "_problems.txt"
            out_path = os.path.join(problem_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(analysis_text)
            results.append({"source": os.path.basename(path), "output": out_name})
        return {"ok": True, "results": results}
