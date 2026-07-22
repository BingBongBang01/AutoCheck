"""
pdf_engine — PDF 번역 엔진의 기능별 모듈 패키지.

translate_pdf.py는 이 패키지의 얇은 파사드(진입점)로, 하위 호환을 위해
`from translate_pdf import extract_segments` 같은 기존 참조가 계속 동작하도록
이 패키지의 심볼들을 재노출한다.

모듈 구성:
  config           - 버전, 경로, 실행 중단(STOP) 제어, 런타임 레지스트리, 기본 모델
  segment          - Segment 데이터클래스 (추출된 텍스트 조각 하나)
  extraction       - PDF에서 텍스트/OCR로 Segment 추출
  batching         - 세그먼트를 배치로 묶고 프롬프트 구성
  providers_cloud  - Anthropic/Gemini/OpenAI 클라이언트, 키 풀, LLM 호출
  providers_local  - Lemonade/Ollama/LM Studio 등 로컬 런타임 관리
  scheduler        - 배치를 provider에 분배해 번역 실행 (재시도/폴백 포함)
  rendering        - 번역문을 PDF에 재삽입 (redaction, 세로쓰기, OCR 배경처리)
  io_utils         - 용어집, 페이지 범위 파싱, 번역 결과 내보내기/가져오기
  filenaming       - 이어서-번역 파일명 규칙, 사이드카 진행정보 JSON
"""
