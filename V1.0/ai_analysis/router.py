"""
AI 분석 라우터. 우선순위: API(설정된 경우) -> 로컬 NPU(Lemonade) -> 규칙기반(항상 성공).
실제 API 키/네트워크가 없어도 프로그램이 절대 멈추지 않도록 규칙기반을 최종 안전망으로 둔다.

원시 로그(raw .txt) 텍스트 분석(raw_log_analyzer.py) 한 경로다 — 청크 분할, 이 PC 밖으로 나가는
요청은 비밀값·주소·장비명을 강제로 가린다(engine.log_masking.mask_for_external_ai).
구조화된 채점 결과(findings)를 분석하던 경로(findings_analyzer.py, core/sanitizer.py)는 채점
파이프라인 퇴역(IMPROVEMENT_PLAN D1)과 함께 없앴다.
"""
try:
    from ai_analysis.raw_log_analyzer import analyze_raw_log_text
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ai_analysis.raw_log_analyzer import analyze_raw_log_text

__all__ = ["analyze_raw_log_text"]
