"""
AI 분석 라우터. 우선순위: API(설정된 경우) -> 로컬 NPU(Lemonade) -> 규칙기반(항상 성공).
실제 API 키/네트워크가 없어도 프로그램이 절대 멈추지 않도록 규칙기반을 최종 안전망으로 둔다.

구현은 두 경로로 분리되어 있다:
  - findings_analyzer.py: 구조화된 findings(scored 결과) 분석 — 마스킹 후 배치 분할
  - raw_log_analyzer.py: 원시 로그(raw .txt) 텍스트 분석 — 마스킹 없이 청크 분할
"""
try:
    from ai_analysis.findings_analyzer import analyze
    from ai_analysis.raw_log_analyzer import analyze_raw_log_text
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ai_analysis.findings_analyzer import analyze
    from ai_analysis.raw_log_analyzer import analyze_raw_log_text

__all__ = ["analyze", "analyze_raw_log_text"]

if __name__ == "__main__":
    sample_scored = [
        {"label": "STP", "status": "IN_PROGRESS", "pass": 3, "total": 14, "results": [
            {"stage": "STP", "device": "Core1", "check": "root_priority_vlan1_core1", "result": "FAIL", "expected": 4096, "actual": 32768},
        ]},
    ]
    # 설정 없음 -> 바로 규칙기반으로 떨어지는지 확인
    result = analyze(sample_scored, ai_config=None)
    print("source:", result["source"])
    print("summary:", result["summary"])

    # 존재하지 않는 API/로컬 설정 -> 둘 다 실패하고 규칙기반으로 폴백되는지 확인
    result2 = analyze(sample_scored, ai_config={"providers": [
        {"type": "api", "api_key_env": "NONEXISTENT_KEY_XYZ"},
        {"type": "local", "endpoint": "http://localhost:19999"},
    ]})
    print("\nsource(폴백 후):", result2["source"])
    print("summary:", result2["summary"])
