"""추출된 텍스트 조각 하나(Segment)를 표현하는 데이터클래스."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Segment:
    seg_id: str
    page: int                      # 0-based
    bbox: tuple                    # (x0, y0, x1, y1)
    text: str                      # 원문
    font_size: float               # 블록 내 최빈 폰트 크기(pt)
    color: str                     # "#rrggbb"
    bold: bool
    needs_translation: bool        # False면 원문 그대로 유지(숫자 전용 블록 등)
    translated: str | None = None
    translation_failed: bool = False   # True면 API 실패/할당량소진 등으로 원문을 그대로 유지한 것
    line_boxes: list[tuple] | None = None  # 원본 각 줄 bbox
    layout_boxes: list[tuple] | None = None  # 병합 전 원본 블록 bbox 보존
    vertical: bool = False          # True면 세로쓰기(일본어 종서 등) - 렌더링 시 writing-mode 적용
    is_ocr: bool = False            # True면 OCR로 얻은 텍스트 - 원본에 텍스트 레이어가 없어 redact 생략
