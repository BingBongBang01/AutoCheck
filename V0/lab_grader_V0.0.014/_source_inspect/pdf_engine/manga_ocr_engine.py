"""
manga-ocr(kha-white/manga-ocr) 선택적 통합.

manga-ocr은 일본어 전용 OCR이다(모델 자체가 일본어로만 학습됨 - 다른 언어는 인식
불가). Tesseract보다 일본어 만화 텍스트(세로쓰기, 후리가나, 이미지 위 텍스트, 장식체
폰트)에 훨씬 강하지만, PyTorch+transformers라는 무거운 의존성(수백MB~1GB)이 필요하고
최초 실행 시 모델(~400MB)을 다운로드해야 한다. 그래서 기본 설치에는 포함하지 않고,
설치돼 있으면 자동으로 활용하는 "있으면 쓰고, 없으면 조용히 Tesseract만 쓰는" 선택적
통합으로 만든다 - GUI의 "manga-ocr 설치(선택)" 버튼으로 별도 설치 가능.

역할 분담: manga-ocr은 오직 "이 이미지에 어떤 텍스트가 있는가"(인식)만 담당한다.
세로/가로 판정, 말풍선 영역 검출, 신뢰도 검사, 렌더링은 전부 기존 파이프라인
(extraction.py, rendering.py)이 그대로 담당 - manga-ocr은 Tesseract를 완전히
대체하는 게 아니라, 일본어 인식 정확도만 필요할 때 국소적으로 대체하는 구조다.
"""
from __future__ import annotations

_engine = None          # 지연 초기화된 MangaOcr 싱글톤 (성공시 캐시)
_load_failed = False    # 한 번 로드 실패하면(미설치 등) 같은 실행 중 재시도 안 함


def is_available() -> bool:
    """manga-ocr 패키지가 설치돼 있는지만 가볍게 확인한다(모델은 아직 안 불러옴)."""
    if _load_failed:
        return False
    try:
        import manga_ocr  # noqa: F401
        return True
    except ImportError:
        return False


def get_engine():
    """
    MangaOcr 인스턴스를 지연 로드해 재사용한다(모델 로드 자체가 몇 초~수십 초 걸리는
    무거운 작업이라 세그먼트/말풍선마다 새로 만들면 안 됨 - 문서 전체에서 한 번만 로드).
    실패하면(패키지 없음, 모델 다운로드 실패 등) None을 반환하고 이후 호출에서 재시도하지
    않는다(매번 몇 초씩 걸리는 실패를 반복하지 않기 위함).
    """
    global _engine, _load_failed
    if _engine is not None:
        return _engine
    if _load_failed:
        return None
    try:
        from manga_ocr import MangaOcr
        print("[manga-ocr] 모델 로드 중(최초 1회, 몇 초~수십 초 소요될 수 있음)...")
        _engine = MangaOcr()
        print("[manga-ocr] 로드 완료")
        return _engine
    except Exception as e:
        print(f"[manga-ocr][경고] 로드 실패, 이후 Tesseract만 사용합니다: {e}")
        _load_failed = True
        return None


def recognize(pil_image) -> str | None:
    """PIL.Image를 받아 인식된 텍스트를 반환한다. 실패하면 None(호출측이 Tesseract로 폴백)."""
    engine = get_engine()
    if engine is None:
        return None
    try:
        text = engine(pil_image)
        return text if text and text.strip() else None
    except Exception as e:
        print(f"[manga-ocr][경고] 인식 실패: {e}")
        return None


def wants_japanese(ocr_lang: str | None) -> bool:
    """이 OCR 언어 설정에 일본어가 포함돼 있는지(manga-ocr을 시도할지 판단용)."""
    langs = (ocr_lang or "").lower().split("+")
    return any(x in langs for x in ("jpn", "jpn_vert"))
