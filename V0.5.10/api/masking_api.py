"""MaskingApiMixin — 'Log Masking' 탭 담당. 최신 점검 회차(runs/<run_id>)의 raw(전체 원본)
또는 problem(필터링된 이상탐지 결과) 중 하나를 소스로 골라, 선택한 항목만
format-preserving 마스킹해 같은 회차의 masked/에 저장한다."""


class MaskingApiMixin:
    def get_mask_options(self):
        """'Log Masking' 탭 체크리스트 — [{"key","label"}, ...] (정확히 8개, 고정 순서)."""
        from engine import log_masking
        return log_masking.get_mask_options()

    def run_log_masking(self, source, categories):
        """source: 'original'(raw 전체) | 'problem'(problem 필터링 결과).
        categories: get_mask_options()의 key 중 사용자가 선택한 것들."""
        if source not in ("original", "problem"):
            return {"error": "잘못된 소스입니다."}
        if not categories:
            return {"error": "마스킹할 항목을 하나 이상 선택하세요."}
        # 결과를 쓸 masked/ 폴더가 필요하므로 create=True. 점검 이력이 없으면 마스킹할 원본도 없다.
        profile_paths = self._active_profile_log_paths(create=True)
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        import os
        from engine import log_masking
        src_dir = profile_paths[source]
        if not os.path.isdir(src_dir) or not os.listdir(src_dir):
            return {"error": "마스킹할 로그가 없습니다. 먼저 점검(또는 로그 분석)을 실행하세요."}
        results = log_masking.run_masking(src_dir, profile_paths["masking"], set(categories))
        return {"ok": True, "results": results}
