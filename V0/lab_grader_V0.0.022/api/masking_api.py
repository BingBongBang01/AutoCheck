"""MaskingApiMixin — 'Log Masking' 탭 담당. 00_orignal_log(전체 원본) 또는
01_problem_log(필터링된 이상탐지 결과) 중 하나를 소스로 골라, 선택한 항목만
format-preserving 마스킹해 02_masking_log에 저장한다."""


class MaskingApiMixin:
    def get_mask_options(self):
        """'Log Masking' 탭 체크리스트 — [{"key","label"}, ...] (정확히 8개, 고정 순서)."""
        from engine import log_masking
        return log_masking.get_mask_options()

    def run_log_masking(self, source, categories):
        """source: 'original'(00_orignal_log 전체) | 'problem'(01_problem_log 필터링 결과).
        categories: get_mask_options()의 key 중 사용자가 선택한 것들."""
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        if source not in ("original", "problem"):
            return {"error": "잘못된 소스입니다."}
        if not categories:
            return {"error": "마스킹할 항목을 하나 이상 선택하세요."}
        from engine import log_masking
        src_dir = profile_paths[source]
        results = log_masking.run_masking(src_dir, profile_paths["masking"], set(categories))
        return {"ok": True, "results": results}
