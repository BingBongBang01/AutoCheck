"""
AI 분석 모듈.
router.py: API(Anthropic/Gemini) -> 로컬NPU -> 규칙기반 순서로 시도, 전부 실패해도 규칙기반은 항상 동작(네트워크 불필요).
rule_based.py: 실제 API 없이도 채점 결과를 문장으로 요약·우선순위 매기는 로직 (여기가 핵심, 항상 동작 보장).
"""
