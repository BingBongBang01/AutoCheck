"""RoundCompareApiMixin — 워크스페이스의 '회차 비교'(IMPROVEMENT_PLAN 4-7, engine/round_compare.py).

활성 고객사의 두 점검(회차 또는 Run)을 고르면 점검 항목·장비 정보·구성도 링크에서 달라진 것을 돌려준다.
"""


class RoundCompareApiMixin:
    def get_round_compare_sources(self, run_id=None):
        """비교할 수 있는 점검과 기본 비교 쌍 — {customer, profile, rounds, base, target}. run_id 를 주면(Run 이력의
        '비교') 그 Run 과 같은 회차의 바로 앞 Run(없으면 앞 회차)이 기본 쌍이다. 앞 점검이 없으면 base 가 None."""
        from engine import round_compare
        customer, profile = self._resolve_active_customer_profile_names()
        if not customer or not profile:
            return {"error": "활성 고객사/프로파일이 없습니다."}
        base, target = round_compare.default_pair(customer, profile, run_id or None)
        return {"customer": customer, "profile": profile, "rounds": round_compare.sources(customer),
                "base": base, "target": target}

    def compare_rounds(self, base, target):
        """두 점검의 차이 — engine/round_compare.compare 의 결과, 찾을 수 없으면 {"error"}."""
        from engine import round_compare
        customer, _profile = self._resolve_active_customer_profile_names()
        if not customer:
            return {"error": "활성 고객사/프로파일이 없습니다."}
        try:
            return round_compare.compare_sources(customer, base or {}, target or {})
        except round_compare.CompareError as exc:
            return {"error": str(exc)}
