"""
번역문을 PDF에 다시 삽입한다: redaction(원문 지우기), 세로쓰기 전용 수동 렌더러,
자동 축소 삽입, OCR 세그먼트의 배경색 추정/안전성 검사(만화 등 복잡한 그림 보호)를 담당.
"""
from __future__ import annotations

import math
import html as html_mod
import re
from collections import defaultdict

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from .segment import Segment

def hex_to_rgb01(hx: str) -> tuple:
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def apply_redactions_safe(page) -> None:
    """텍스트만 제거하고 이미지/벡터 그래픽은 보존. 구버전 시그니처 폴백 포함."""
    try:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    except (TypeError, AttributeError):
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)


def _clear_rect_text(page, rect) -> None:
    """
    렌더링 재시도 사이에 그 영역의 텍스트만 지운다(이미지/배경 그래픽은 보존).
    insert_htmlbox/insert_textbox가 "실패"를 반환해도 이미 부분적으로 그려놓은
    글자가 페이지에 남아있는 경우가 있어(mupdf Story가 들어간 만큼은 실제로 그림),
    다음 시도가 그 위에 겹쳐 그려져 글자가 이중으로 보이는 문제가 있었다(실제 확인:
    원문 첫 글자가 번역문 위에 겹쳐 보임). 재시도 전에 항상 이 영역을 비운다.
    """
    try:
        page.add_redact_annot(pymupdf.Rect(rect), fill=False)
        apply_redactions_safe(page)
    except Exception:
        pass


def _delete_overlapping_small_images(page, bbox, max_area_ratio: float = 0.5) -> None:
    """
    세그먼트 bbox와 거의 겹치는 "작은" 이미지를 삭제한다.

    실제로 확인된 문제: 어떤 PDF 생성 도구는 특정 글자를 텍스트로 넣으면서 동시에
    그 글자와 똑같은 위치에 SMask 처리된 작은 이미지(예: 그 글자 모양의 검은
    래스터)도 겹쳐서 렌더링해둔다. 우리는 텍스트만 redact하므로(이미지는 원본 배경/
    사진을 보호하기 위해 보존하는 정책), 이런 경우 그 중복 이미지가 지워지지 않고
    남아 새로 삽입한 번역문 위에 겹쳐 보인다(실제 사용자 리포트로 확인됨).
    "그 세그먼트 영역 안에 딱 들어맞는 작은 이미지"만 골라서 지운다 - 세그먼트보다
    훨씬 큰 이미지(원본 삽화/사진 등 정당한 배경)는 절대 건드리지 않는다.
    """
    seg_rect = pymupdf.Rect(bbox)
    seg_area = max(seg_rect.width * seg_rect.height, 1e-6)
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return
    for info in infos:
        img_bbox = info.get("bbox")
        xref = info.get("xref", 0)
        if not img_bbox or not xref:
            continue
        img_rect = pymupdf.Rect(img_bbox)
        inter = seg_rect & img_rect
        inter_area = max(inter.width, 0) * max(inter.height, 0)
        img_area = max(img_rect.width * img_rect.height, 1e-6)
        if inter_area / img_area > 0.7 and img_area <= seg_area * max_area_ratio:
            try:
                page.delete_image(xref)
            except Exception:
                pass


def _normalize_translation_text(text: str) -> str:
    """모델이 만든 불필요한 공백만 정리하고 명시적 줄바꿈은 보존한다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


_CJK_FALLBACK_FONTNAME = "cjkfb0"
_cjk_font_buffer = None
_cjk_registered_pages: set = set()


def _ensure_cjk_font_embedded(page) -> str:
    """
    세로쓰기 렌더러가 쓸 CJK 폰트를 이 페이지에 실제로 임베드하고, 등록한 폰트 이름을
    반환한다.

    중요: page.insert_text(fontname="korea", ...)처럼 pymupdf 내장 별칭 이름을 그대로
    쓰면, 실제 폰트 파일이 PDF에 임베드되지 않고 "Dotum"이라는 시스템 폰트를 참조만
    하는 상태가 된다(실제 확인: get_fonts()에서 ext='n/a', embedded=0). 이러면 우리
    개발 환경(대체 폰트가 깔려 있어 그런대로 렌더링됨)에서는 문제가 안 보이지만, 실제
    사용자 PDF 뷰어(Dotum이 없는 환경)에서는 글자가 통째로 안 보이는 심각한 호환성
    문제가 된다(실제 사용자 리포트로 확인됨 - 세로쓰기 번역문이 안 보이고 문장부호만
    남는 증상).
    해결: pymupdf.Font()로 실제 폰트 바이너리(buffer)를 얻어 page.insert_font()로
    명시적으로 임베드한다(get_fonts()에서 ext='ttf'로 바뀌는 것으로 실제 임베드 확인됨).
    폰트 버퍼는 무거우므로 모듈 전역에서 한 번만 로드하고, 페이지별로는 한 번만
    등록한다(같은 페이지에 중복 등록 방지).
    """
    global _cjk_font_buffer
    if _cjk_font_buffer is None:
        _cjk_font_buffer = pymupdf.Font("china-s").buffer  # 한중일 통합 폴백(한글 포함 확인됨)
    key = id(page)
    if key not in _cjk_registered_pages:
        page.insert_font(fontname=_CJK_FALLBACK_FONTNAME, fontbuffer=_cjk_font_buffer)
        _cjk_registered_pages.add(key)
    return _CJK_FALLBACK_FONTNAME


def _insert_vertical_text_manual(page, rect, text: str, seg: Segment, font_scale: float) -> bool:
    """
    세로쓰기(일본어/한국어 종서) 전용 렌더러.

    insert_htmlbox의 rotate=90 파라미터는 텍스트 줄 전체를 하나의 강체로 90도
    돌려버려서 글자 자체가 옆으로 누운 채 나온다(고개를 기울여야 읽히고, 읽는
    방향도 위->아래가 아니라 회전된 좌->우가 됨) - 진짜 세로쓰기가 아니다.
    CSS 'writing-mode: vertical-rl'도 mupdf Story가 지원하지 않는 것으로 확인됐다.
    그래서 글자를 하나씩 직접 배치해 각 글자는 똑바로 세운 채 위->아래로 쌓는다
    (실제 세로쓰기와 동일한 방식). 한 컬럼(세로줄)에 다 안 들어가면 오른쪽에서
    왼쪽으로 새 컬럼을 만들어 이어간다(전통적 세로쓰기 진행 방향과 동일 - 원본도
    이 순서로 배치돼 있었다). 원래 bbox의 상단에 딱 붙여 시작해 원본과 같은
    위치를 유지한다.
    """
    plain = _normalize_translation_text(text).replace("\n", "")
    # 세로쓰기에서는 공백이 별도 칸을 차지하지 않아야 한다 - 공백마다 한 칸씩 비우면
    # (원래 코드가 그랬음) 번역문에 있는 여러 개의 공백(한국어는 띄어쓰기가 잦음)만큼
    # 예상보다 훨씬 많은 세로 공간이 필요해져서, 텍스트가 여러 조각으로 쪼개진 것처럼
    # 큰 간격이 생기거나(원래 bbox 상단에 붙어야 할 텍스트가 아래로 밀려 보임의 원인),
    # 그 공간을 맞추려다 폰트가 불필요하게 축소되는 문제가 있었다. 완전히 제거한다.
    plain = plain.replace(" ", "").replace("\u3000", "")
    if not plain:
        return True

    rect = pymupdf.Rect(rect)
    color = hex_to_rgb01(seg.color)
    base_fs = max(4.0, seg.font_size * font_scale)
    min_fs = max(3.5, base_fs * 0.4)
    cjk_fontname = _ensure_cjk_font_embedded(page)

    size = base_fs
    while size >= min_fs:
        row_h = size * 1.15   # 글자 하나가 차지하는 세로 칸 높이
        col_w = size * 1.15   # 컬럼(세로줄) 하나의 가로 폭
        rows_per_col = max(1, int(rect.height // row_h))
        n_cols = math.ceil(len(plain) / rows_per_col)
        needed_w = n_cols * col_w
        if needed_w <= rect.width + 0.5:
            try:
                idx = 0
                for c_i in range(n_cols):
                    # 오른쪽 끝 컬럼부터 시작해 왼쪽으로 진행 (세로쓰기 전통적 읽기 순서)
                    x = rect.x1 - col_w * (c_i + 1) + col_w * 0.1
                    y = rect.y0 + size  # 첫 글자의 baseline (bbox 상단에 붙임)
                    for _ in range(rows_per_col):
                        if idx >= len(plain):
                            break
                        ch = plain[idx]
                        page.insert_text((x, y), ch, fontsize=size,
                                         fontname=cjk_fontname, color=color)
                        y += row_h
                        idx += 1
                    if idx >= len(plain):
                        break
                return True
            except Exception:
                _clear_rect_text(page, rect)  # 이 시도의 부분 렌더링 잔재 제거
        size *= 0.92
    return False


def _insert_text_in_rect(page, rect, text, seg: Segment, font_scale: float) -> bool:
    """
    V3.8: 원래 bbox 밖으로 확장하지 않는다.
    확장은 인접 목차 항목/다단 텍스트를 침범해 겹침을 만들기 때문이다.
    원본 블록의 위치/폭/높이를 고정하고 글꼴 크기만 단계적으로 줄인다.
    """
    text = _normalize_translation_text(text)
    if not text:
        return True

    rect = pymupdf.Rect(rect)
    base_fs = max(4.0, seg.font_size * font_scale)
    min_fs = max(3.5, base_fs * 0.45)
    color = hex_to_rgb01(seg.color)
    weight = "bold" if seg.bold else "normal"

    # 세로쓰기는 전용 수동 렌더러를 최우선 사용 (아래 rotate=90 방식은 글자가
    # 통째로 누워버리는 문제가 있어 폴백으로만 남겨둠).
    if seg.vertical:
        if _insert_vertical_text_manual(page, rect, text, seg, font_scale):
            return True
        _clear_rect_text(page, rect)  # 실패 시도의 부분 렌더링 잔재 제거 (아래 참고)

    # HTML 삽입: CJK 폰트 폴백과 자동 맞춤을 우선 사용.
    if hasattr(page, "insert_htmlbox"):
        body = html_mod.escape(text).replace("\n", "<br>")
        # pt를 사용해 PDF 원본 font_size와 단위를 맞춘다 (기존 px 사용은 크기 왜곡 원인).
        css = ("* {margin:0;padding:0;font-family:sans-serif;"
               f"font-size:{base_fs:.2f}pt;color:{seg.color};font-weight:{weight};"
               "line-height:1.08;}")
        # 주의: CSS 'writing-mode: vertical-rl'는 mupdf Story가 지원하지 않는다(CSS2 수준까지만
        # 지원 확인됨). rotate=90은 줄 전체를 강체로 돌려 글자가 누운 채 나오므로
        # (실사용 테스트에서 확인) 세로쓰기의 정식 방법이 아니다 - 위 수동 렌더러 실패시의
        # 최후 폴백으로만 사용한다.
        rotate = 90 if seg.vertical else 0
        try:
            spare, scale = page.insert_htmlbox(
                rect, f"<div>{body}</div>", css=css, scale_low=0.45, rotate=rotate
            )
            if spare is None or spare >= 0:
                return True
            # spare<0 = 다 못 들어갔다는 뜻이지만, mupdf Story는 들어간 만큼은 이미
            # 실제로 그려놓은 상태다("부분 렌더링"). 이걸 "실패"로 보고 아래 폴백으로
            # 넘어가면, 이미 그려진 부분 위에 폴백 결과가 겹쳐져 글자가 이중으로 보이는
            # 문제가 있었다(실제 확인됨 - 원문 첫 글자가 번역문 위에 겹쳐 보임). 폴백
            # 전에 이 영역을 다시 지운다.
            _clear_rect_text(page, rect)
        except Exception:
            _clear_rect_text(page, rect)  # 예외 시에도 부분 렌더링 가능성 있으므로 정리

    # 폴백: bbox 안에 들어갈 때까지 점진 축소.
    cjk_fontname = _ensure_cjk_font_embedded(page)
    size = base_fs
    while size >= min_fs:
        try:
            rc = page.insert_textbox(
                rect, text, fontsize=size, fontname=cjk_fontname,
                color=color, lineheight=1.08
            )
            if rc >= 0:
                return True
            _clear_rect_text(page, rect)  # 이 시도도 부분 렌더링 후 실패했을 수 있음
        except Exception:
            _clear_rect_text(page, rect)
        size *= 0.92
    return False


def insert_translated_text(page, seg: Segment, font_scale: float) -> bool:
    # V3.8에서는 물리 블록 병합을 하지 않으므로 항상 원래 bbox 하나에 삽입한다.
    text = (seg.translated or "").replace("|||SUB_SEPARATOR|||", "\n").strip()
    return _insert_text_in_rect(page, seg.bbox, text, seg, font_scale)


def _analyze_ocr_background(page, bbox) -> tuple[bool, tuple, float]:
    """OCR 삽입 안전성 검사: 만화에서는 밝고 균일한 말풍선/여백만 수정 허용."""
    try:
        rect=pymupdf.Rect(bbox)
        pix=page.get_pixmap(clip=rect,dpi=96,alpha=False)
        n=max(1,pix.n); raw=pix.samples; vals=[]; colors=[]
        pixels=max(1,len(raw)//n); stride=max(1,pixels//3000)
        for px in range(0,pixels,stride):
            i=px*n
            if i+2>=len(raw): break
            rgb=(raw[i],raw[i+1],raw[i+2])
            vals.append(sum(rgb)/3)
            colors.append(tuple((v//32)*32 for v in rgb))
        if len(vals)<12:return False,(1,1,1),0.0
        mean=sum(vals)/len(vals)
        bright=sum(v>=210 for v in vals)/len(vals)
        # OCR bbox에는 검은 글자 픽셀이 있으므로 dominant color만으로 판정하지 않는다.
        # 평균 밝기와 밝은 픽셀 비율을 함께 사용해 그림 영역을 차단한다.
        safe=mean>=185 and bright>=0.58
        return safe,(1,1,1),bright
    except Exception:
        return False,(1,1,1),0.0



def rebuild_pdf(doc, segments: list[Segment], font_scale: float) -> int:
    by_page: dict[int, list[Segment]] = defaultdict(list)
    for s in segments:
        if s.needs_translation and s.translated:
            by_page[s.page].append(s)

    truncated = 0
    for pno in sorted(by_page):
        page = doc[pno]
        # OCR로 얻은 세그먼트는 원본에 텍스트 레이어가 없다(이미지 위의 픽셀일 뿐이라
        # redact로 지울 대상 자체가 없음). 대신 배경색으로 그 영역을 덮어 원문 픽셀을
        # 가린 뒤 번역문을 얹는다 - 안 그러면 원어 글자가 번역문과 겹쳐 보인다.
        ocr_segs = [s for s in by_page[pno] if s.is_ocr]
        redactable = [s for s in by_page[pno] if not s.is_ocr]
        for s in redactable:
            page.add_redact_annot(pymupdf.Rect(s.bbox), fill=False)
            # 텍스트와 겹치는 작은 중복 이미지가 있으면 함께 제거 (원본 배경/사진처럼
            # 세그먼트보다 훨씬 큰 이미지는 이 조건에 안 걸려 보존됨 - 실제 확인된 문제:
            # 일부 PDF 생성 도구가 특정 글자를 텍스트+이미지로 이중 렌더링해두는 경우가
            # 있어, 텍스트만 지우면 이미지가 남아 번역문과 겹쳐 보였음)
            _delete_overlapping_small_images(page, s.bbox)
        if redactable:
            apply_redactions_safe(page)
        safe_ocr_ids=set()
        for s in ocr_segs:
            safe,bg,uniformity=_analyze_ocr_background(page,s.bbox)
            if not safe:
                print(f"  [OCR 안전장치] {s.seg_id}: 복잡한 이미지 영역 "
                      f"(단색도={uniformity:.2f}) -> 원본 보호를 위해 덮기/번역문 삽입 생략")
                continue
            pad=max(0.5,min(1.5,s.font_size*0.08))
            cover_rect=pymupdf.Rect(s.bbox)+(-pad,-pad,pad,pad)
            page.draw_rect(cover_rect,color=None,fill=bg,overlay=True)
            safe_ocr_ids.add(s.seg_id)
        for s in by_page[pno]:
            if s.is_ocr and s.seg_id not in safe_ocr_ids:
                continue
            if not insert_translated_text(page, s, font_scale):
                truncated += 1
                print(f"  [경고] {s.seg_id}: 번역문이 원래 영역보다 길어 최대 축소로도 넘칠 수 있음")
    return truncated