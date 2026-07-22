"""
PDF에서 번역 대상 텍스트(Segment)를 추출한다.

일반 텍스트 레이어 추출 + 스캔본(이미지) 자동 감지 후 OCR 폴백 + 표/세로쓰기
레이아웃 보호(셀 단위 분리, 세로쓰기 판정)를 모두 이 모듈이 담당한다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from .config import LETTER_RE
from .segment import Segment

_OCR_LANG_MAP = {
    "english": "eng", "en": "eng",
    "korean": "kor", "ko": "kor", "한국어": "kor",
    "japanese": "jpn+jpn_vert", "ja": "jpn+jpn_vert", "일본어": "jpn+jpn_vert",
    "chinese": "chi_sim", "chinese (simplified)": "chi_sim", "zh": "chi_sim",
    "chinese (traditional)": "chi_tra",
    "french": "fra", "german": "deu", "spanish": "spa",
}


def resolve_ocr_lang(source_lang: str, explicit_ocr_lang: str | None) -> str | None:
    """
    명시적으로 --ocr-lang을 줬으면 그걸 우선하고, 없으면 --source-lang에서 자동 매핑한다.
    source_lang이 'auto'(자동 인식)이거나 매핑표에 없는 언어면 None을 반환한다 - "언어를
    모르는 채로 OCR을 시도하는 것"은 실제로 위험하다: Tesseract가 어떤 언어팩을 써야
    할지도 모르는 상태에서 무작정 영어로 강행하면, 영어 판정 필터("이 글자가 라틴
    알파벳인가")는 CJK 범위 검사와 달리 판별력이 거의 없어서(Tesseract의 eng 모델이
    만화 등 복잡한 그림에서 뽑아낸 아무 글자 나열이든 전부 라틴 알파벳이므로 필터를
    그대로 통과함) 노이즈가 훨씬 심하게 새어나간다(실제로 확인된 문제). 그래서 언어를
    모르면 OCR 자체를 시도하지 않는 게 더 안전하다 - 호출측(extract_segments)이 None을
    보면 OCR을 건너뛰고 사용자에게 언어를 명시하라고 안내한다.
    """
    if explicit_ocr_lang:
        return explicit_ocr_lang
    return _OCR_LANG_MAP.get((source_lang or "").strip().lower())


def find_tessdata_dir(explicit: str | None = None) -> str | None:
    """
    Tesseract의 언어 데이터(tessdata) 폴더를 찾는다. pymupdf의 자체 자동탐지는
    유닉스 계열에서 'whereis tesseract-ocr'라는 명령 결과에만 의존하는데, 실행파일
    이름이 배포판/설치방식마다 다르면(예: 'tesseract'만 있고 'tesseract-ocr'는 없음,
    Windows는 애초에 'whereis'가 없음) 이 탐지가 실패해서 Tesseract가 실제로 정상
    설치돼 있어도 "Tesseract is not installed"라는 오류가 난다. 그래서 우리가 직접
    더 폭넓게 탐색한다.
    우선순위: 1) 사용자가 --tessdata-dir로 직접 지정  2) TESSDATA_PREFIX 환경변수
    3) tesseract 실행파일 위치 기준 형제/하위 tessdata 폴더  4) OS별로 흔히 설치되는 경로.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_prefix = os.environ.get("TESSDATA_PREFIX")
    if env_prefix:
        candidates.append(Path(env_prefix))

    exe = shutil.which("tesseract")
    if exe:
        exe_dir = Path(exe).resolve().parent
        candidates += [
            exe_dir / "tessdata",             # Windows 표준: <설치폴더>\tessdata
            exe_dir.parent / "tessdata",
            exe_dir.parent / "share" / "tessdata",
            exe_dir.parent / "share" / "tesseract-ocr" / "tessdata",
        ]

    if os.name == "nt":
        candidates += [
            Path("C:/Program Files/Tesseract-OCR/tessdata"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tessdata"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tessdata",
        ]
    else:
        import glob
        candidates += [Path(p) for p in glob.glob("/usr/share/tesseract-ocr/*/tessdata")]
        candidates += [
            Path("/usr/share/tessdata"),
            Path("/usr/local/share/tessdata"),
            Path("/opt/homebrew/share/tessdata"),
        ]

    for c in candidates:
        try:
            if c and c.is_dir() and any(c.glob("*.traineddata")):
                return str(c)
        except Exception:
            continue
    return None



# 정상적인 말풍선/캡션 텍스트의 폰트 크기는 보통 8~16pt 범위다. 실제 만화 샘플로 확인한
# 결과, OCR이 서로 다른 그림 영역을 하나의 줄로 잘못 합쳐 만든 오탐은 거의 전부 이 값을
# 넘었다(20pt 초과). 절대적 기준이라 완벽하진 않지만 실사용에서 노이즈를 크게 줄여준다.
OCR_MAX_PLAUSIBLE_FONT_SIZE = 20.0


def _sanitize_ocr_text(text: str, ocr_lang: str) -> str:
    text=(text or "").strip()
    langs = (ocr_lang or "").lower().split("+")
    # 만화 오탐이 흔히 "라틴/기호 나열"로 나오는 패턴을 차단하는 필터인데, 이건 일본어만
    # 기대할 때 유효하다. 여러 언어를 함께 지정한 경우(예: "jpn+jpn_vert+eng+kor" - 실제
    # 다국어 문서에서 이렇게 지정하는 게 정상적인 사용법이다), "jpn"이 문자열에 들어있다는
    # 이유만으로 이 필터를 적용하면 정상적인 영어 텍스트까지 통째로 지워지는 심각한 버그가
    # 있었다(실제 확인: "WATCH OUT! THE ENEMY..."처럼 완벽히 인식된 영어 문장이 전부 빈
    # 문자열로 사라짐). eng이 명시적으로 함께 지정됐으면 이 필터를 건너뛴다.
    if any(x in langs for x in ("jpn", "jpn_vert")) and "eng" not in langs:
        text=re.sub(r"[A-Za-z]{2,}", "", text)
        text=re.sub(r"[^\u3040-\u30ff\u3400-\u9fffー々〆ヵヶ、。！？…\s]", "", text)
        text=re.sub(r"\s+", "", text)
    return text.strip()


def _ocr_script_plausibility(text: str, ocr_lang: str) -> tuple[bool, float, str]:
    """OCR 쓰레기 텍스트를 기대 언어 문자 비율로 차단한다."""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False, 0.0, "empty"
    lang = (ocr_lang or "eng").lower().split("+")
    def jp(c): return ("\u3040" <= c <= "\u30ff") or ("\u3400" <= c <= "\u9fff")
    def ko(c): return ("\uac00" <= c <= "\ud7a3") or ("\u1100" <= c <= "\u11ff")
    def latin(c): return c.isascii() and c.isalpha()

    # 지정된 언어별 검사 기준을 모두 모은다. 예전엔 elif 체인이라 "jpn"이 리스트에
    # 있으면 무조건 일본어 문자만 검사하고 영어/한국어는 아예 확인을 안 했다 - 다국어를
    # 함께 지정하면(예: "jpn+jpn_vert+eng+kor", 실제 다국어 문서에서는 정상적인 사용법)
    # 정상적인 영어/한국어 텍스트까지 전부 "일본어 문자가 0개"라며 탈락시키는 심각한
    # 버그였다(실제 확인: "WATCH OUT! THE ENEMY..." 완벽 인식 결과가 script=0/12로
    # 실패 판정됨). 지정된 언어 중 "하나라도" 매치하는 문자면 인정하도록 합집합으로
    # 판정한다.
    checks = []
    if any(x in lang for x in ("jpn", "jpn_vert")):
        checks.append((jp, .65, 3))
    if "kor" in lang:
        checks.append((ko, .42, 2))
    if "eng" in lang or not checks:
        checks.append((latin, .50, 2))
    if not any(x in lang for x in ("jpn", "jpn_vert", "kor", "eng")):
        # 알 수 없는 언어는 문자/숫자가 충분한지만 보수적으로 검사
        checks = [(lambda c: c.isalnum(), .45, 2)]

    matches = sum(1 for c in compact if any(fn(c) for fn, _, _ in checks))
    threshold = min(t for _, t, _ in checks)
    min_matches = min(m for _, _, m in checks)
    ratio = matches / max(1, len(compact))
    # 1글자 OCR은 만화 효과선/노이즈 오탐 가능성이 너무 높아 제외
    ok = matches >= min_matches and ratio >= threshold
    return ok, ratio, f"script={matches}/{len(compact)}"
def _find_bubble_regions(page, dpi: int = 200) -> list[tuple]:
    """
    만화 말풍선처럼 "텍스트를 담는 밝은(흰색) 배경의 독립된 영역"을 이미지 분석으로
    찾는다. 전체 페이지를 통째로 OCR하면 그림(효과선/스크린톤/후리가나 등)까지 글자로
    오인하는 노이즈가 크므로, 먼저 "글자가 있을 법한 영역"만 좁혀서 그 영역만 OCR하면
    정확도가 개선될 여지가 있다(주변 그림의 간섭이 줄어듦).
    opencv/numpy가 없으면 빈 리스트를 반환해 호출측이 기존 전체 페이지 OCR로 폴백한다.
    """
    try:
        import numpy as np
        import cv2
    except ImportError:
        return []
    try:
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 and img.shape[2] >= 3 else img
        _, binary = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY)
        num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    except Exception:
        return []

    page_area = gray.shape[0] * gray.shape[1]
    regions = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if area < 3000 or area > page_area * 0.15:
            continue
        aspect = w / max(h, 1)
        if aspect < 0.15 or aspect > 6:
            continue
        x0, y0 = x * 72 / dpi, y * 72 / dpi
        x1, y1 = (x + w) * 72 / dpi, (y + h) * 72 / dpi
        regions.append((x0, y0, x1, y1))
    return regions


def _ocr_crop_region(page, rect: tuple, ocr_lang: str, tessdata: str | None,
                     dpi: int = 300) -> dict:
    """
    페이지 전체가 아니라 지정한 영역(rect)만 잘라 OCR한다. 가능하면 tesseract CLI를
    직접 호출해 PSM(페이지 분할 모드)을 여러 개 시도하고 그중 최선을 채택한다(아래
    _ocr_crop_region_cli 참고) - pymupdf의 내장 OCR API(get_textpage_ocr)는 PSM을
    노출하지 않는데, 실제 테스트로 PSM 선택이 인식 정확도에 큰 영향을 준다는 걸
    확인했다(특히 한국어/영어 세로쓰기처럼 전용 세로쓰기 모델이 없는 언어). tesseract
    실행파일이 없으면 기존 pymupdf 내장 OCR로 폴백한다.
    """
    if shutil.which("tesseract"):
        result = _ocr_crop_region_cli(page, rect, ocr_lang, tessdata, dpi)
        if result["blocks"]:
            return result
    return _ocr_crop_region_pymupdf(page, rect, ocr_lang, tessdata, dpi)


def _ocr_crop_region_pymupdf(page, rect: tuple, ocr_lang: str, tessdata: str | None,
                             dpi: int = 300) -> dict:
    """페이지 전체가 아니라 지정한 영역(rect)만 별도의 임시 1페이지 PDF로 만들어
    pymupdf 내장 OCR로 인식한다(tesseract CLI를 못 쓸 때의 폴백)."""
    pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=dpi)
    tmp_doc = pymupdf.open()
    try:
        tmp_page = tmp_doc.new_page(width=pix.width * 72 / dpi, height=pix.height * 72 / dpi)
        tmp_page.insert_image(tmp_page.rect, pixmap=pix)
        kwargs = {"full": True, "language": ocr_lang, "dpi": dpi}
        if tessdata:
            kwargs["tessdata"] = tessdata
        ocr_tp = tmp_page.get_textpage_ocr(**kwargs)
        return tmp_page.get_text("dict", textpage=ocr_tp, sort=True)
    finally:
        tmp_doc.close()


# 시도할 PSM(Page Segmentation Mode) 후보. 실제 테스트로 확인한 우선순위:
#   6 = 균일한 텍스트 블록 하나로 가정 (세로쓰기 말풍선 전체에서 가장 나은 결과를 보임)
#   3 = 완전 자동 페이지 분할(기본값)
#   11 = 희소한 텍스트(정렬 무시) - 점수는 높게 나오지만 낱글자 단위라 실제 정확도는
#        낮을 수 있어 후순위(스크립트 신뢰도 점수만으로는 "형식만 맞고 순서가 틀린"
#        결과를 걸러내지 못하는 한계가 있음을 실제 확인함 - 그래도 6/3이 완전히 실패한
#        경우의 마지막 보루로는 남겨둠)
_OCR_PSM_CANDIDATES = (6, 3, 11)


def _ocr_crop_region_cli(page, rect: tuple, ocr_lang: str, tessdata: str | None,
                         dpi: int = 300) -> dict:
    """
    tesseract CLI를 직접 호출해 PSM을 통제하고, 여러 PSM 중 스크립트 신뢰도가 가장
    높은 결과를 채택한다. TSV 출력으로 줄 단위 위치정보를 복원해, 이 함수를 호출하는
    나머지 파이프라인(문단/표 판정, 세로쓰기 판정, 폰트크기 검사 등)이 기존 pymupdf
    dict 구조를 그대로 쓸 수 있게 맞춘다.
    """
    pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=dpi)
    fd, img_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        pix.save(img_path)
        best_score, best_tsv = -1.0, None
        for psm in _OCR_PSM_CANDIDATES:
            try:
                cmd = ["tesseract", img_path, "stdout", "--psm", str(psm), "-l", ocr_lang]
                if tessdata:
                    cmd += ["--tessdata-dir", tessdata]
                cmd.append("tsv")
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                tsv = r.stdout
                rows = [ln.split("\t") for ln in tsv.strip().split("\n")[1:] if ln.strip()]
                plain = "".join(row[11] for row in rows if len(row) >= 12 and row[11].strip())
                if not plain:
                    continue
                plausible, ratio, _reason = _ocr_script_plausibility(plain, ocr_lang)
                score = ratio if plausible else ratio * 0.2
                if score > best_score:
                    best_score, best_tsv = score, tsv
            except Exception:
                continue
    finally:
        try:
            os.unlink(img_path)
        except Exception:
            pass

    if not best_tsv:
        return {"blocks": []}
    return _tsv_to_dict(best_tsv, dpi)


def _tsv_to_dict(tsv: str, dpi: int) -> dict:
    """
    tesseract TSV 출력을 (block,par,line) 단위로 묶어 pymupdf get_text('dict')와
    호환되는 최소 구조로 변환한다. 반환하는 bbox는 "크롭된 이미지 기준 상대좌표"다
    (페이지 절대좌표 아님) - 호출측인 _build_fake_blocks_from_bubbles가 이 좌표에
    영역의 오프셋(rect[0], rect[1])을 더해 절대좌표로 바꾸므로, 여기서 오프셋을
    더하면 이중으로 적용되는 버그가 된다(실제로 이 문제로 좌표가 크게 어긋난 것을
    확인 후 수정함).

    word 단위로 쪼개는 것도 시도해봤으나(세로쓰기를 가로로 오인해 여러 글자가 뭉친
    "줄"이 생기는 문제를 완화하려는 의도), 실측 결과 오히려 통과율이 더 낮아져
    (17% -> 0%) line 단위 그대로 쓰는 게 더 낫다는 것을 확인했다.
    """
    rows = [ln.split("\t") for ln in tsv.strip().split("\n")[1:] if ln.strip()]
    scale = 72.0 / dpi
    lines_map: dict = {}
    for row in rows:
        if len(row) < 12:
            continue
        _level, _page_n, block_n, par_n, line_n, _word_n, left, top, width, height, _conf, text = row[:12]
        if not text.strip():
            continue
        try:
            left, top, width, height = float(left), float(top), float(width), float(height)
        except ValueError:
            continue
        x0, y0 = left * scale, top * scale
        x1, y1 = x0 + width * scale, y0 + height * scale
        lines_map.setdefault((block_n, par_n, line_n), []).append((x0, y0, x1, y1, text))

    lines = []
    for _key, words in sorted(lines_map.items()):
        lx0 = min(w[0] for w in words)
        ly0 = min(w[1] for w in words)
        lx1 = max(w[2] for w in words)
        ly1 = max(w[3] for w in words)
        text = "".join(w[4] for w in words)
        size = max(ly1 - ly0, 1.0)
        lines.append({
            "bbox": (lx0, ly0, lx1, ly1),
            "dir": (1.0, 0.0),
            "spans": [{"text": text, "size": size, "color": 0, "font": ""}],
        })
    if not lines:
        return {"blocks": []}
    bx0 = min(l["bbox"][0] for l in lines)
    by0 = min(l["bbox"][1] for l in lines)
    bx1 = max(l["bbox"][2] for l in lines)
    by1 = max(l["bbox"][3] for l in lines)
    return {"blocks": [{"type": 0, "bbox": (bx0, by0, bx1, by1), "lines": lines}]}


def _infer_vertical_from_lines(lines: list[dict], rect: tuple) -> bool:
    """
    줄들의 위치 퍼짐(세로로 넓게 퍼지면 세로쓰기)으로 방향을 추정한다. manga-ocr은
    텍스트만 반환하고 방향 정보를 안 주므로, 같은 영역에서 Tesseract가 뽑은 줄
    구조(신뢰도가 낮아 텍스트 자체는 버려도 위치 정보는 여전히 유효)를 재사용해
    판정한다. 그마저도 없으면 영역 자체의 세로/가로 비율로 근사한다.
    """
    if len(lines) >= 2:
        xs = [ln["bbox"][0] for ln in lines]
        ys = [ln["bbox"][1] for ln in lines]
        x_spread = max(xs) - min(xs)
        y_spread = max(ys) - min(ys)
        if x_spread > 0 or y_spread > 0:
            return y_spread > x_spread * 1.2
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    return h > w * 1.1


def _build_fake_blocks_from_bubbles(page, regions: list[tuple], ocr_lang: str,
                                    tessdata: str | None) -> list[dict]:
    """
    검출된 말풍선 영역들을 각각 크롭 OCR한 뒤, "페이지 전체를 get_text('dict')로 뽑은 것과
    같은 형태의 블록 리스트"로 재구성한다. 이렇게 하면 문단/표/세로쓰기 판정, OCR 안전
    필터 등 기존 처리 파이프라인을 그대로 재사용할 수 있다 - 영역 하나 = 블록 하나로
    대응되므로 서로 다른 말풍선의 텍스트가 섞일 일도 없다.

    일본어가 요청됐고(--ocr-lang에 jpn/jpn_vert 포함) manga-ocr이 설치돼 있으면, 그
    영역의 텍스트 인식 자체는 manga-ocr(일본어 전용, Tesseract보다 만화 텍스트에 훨씬
    강함)로 대체한다. 다만 manga-ocr은 방향(세로/가로) 정보를 안 주므로, 같은 영역의
    Tesseract 결과(줄 위치 구조)를 방향 판정에만 재사용한다 - "인식"은 manga-ocr,
    "구조 파악"은 Tesseract라는 역할 분담이다. manga-ocr이 없거나 실패하면 자동으로
    기존 Tesseract 결과를 그대로 쓴다(완전히 투명한 폴백).
    """
    from . import manga_ocr_engine
    try_manga_ocr = manga_ocr_engine.wants_japanese(ocr_lang) and manga_ocr_engine.is_available()

    fake_blocks = []
    for rect in regions:
        try:
            region_data = _ocr_crop_region(page, rect, ocr_lang, tessdata)
        except Exception:
            region_data = {"blocks": []}
        tesseract_lines = [ln for b in region_data.get("blocks", []) if b.get("type") == 0
                          for ln in b.get("lines", [])]

        mocr_text = None
        if try_manga_ocr:
            try:
                import io
                from PIL import Image
                pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                mocr_text = manga_ocr_engine.recognize(img)
            except Exception:
                mocr_text = None

        if mocr_text:
            is_vert = _infer_vertical_from_lines(tesseract_lines, rect)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            approx_size = max(w if is_vert else h, 4.0) * 0.9
            new_lines = [{
                "bbox": (0.0, 0.0, w, h),
                "dir": (0.0, -1.0) if is_vert else (1.0, 0.0),
                "spans": [{"text": mocr_text, "size": approx_size, "color": 0, "font": ""}],
            }]
        else:
            new_lines = tesseract_lines

        if not new_lines:
            continue
        rx0, ry0 = rect[0], rect[1]
        shifted_lines = []
        for ln in new_lines:
            lb = ln.get("bbox", (0, 0, 0, 0))
            shifted = dict(ln)
            shifted["bbox"] = (lb[0] + rx0, lb[1] + ry0, lb[2] + rx0, lb[3] + ry0)
            shifted_lines.append(shifted)
        bx0 = min(l["bbox"][0] for l in shifted_lines)
        by0 = min(l["bbox"][1] for l in shifted_lines)
        bx1 = max(l["bbox"][2] for l in shifted_lines)
        by1 = max(l["bbox"][3] for l in shifted_lines)
        fake_blocks.append({"type": 0, "bbox": (bx0, by0, bx1, by1), "lines": shifted_lines})
    return fake_blocks


def _estimate_image_area_ratio(page) -> float:
    """페이지에서 이미지가 차지하는 대략적인 면적 비율(0~1). 이 값이 크면(예: >0.5)
    사진/스캔/만화처럼 이미지가 페이지 대부분을 차지하는 문서로 볼 수 있다."""
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return 0.0
        total = 0.0
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                r = pymupdf.Rect(bbox)
                total += max(0.0, r.width) * max(0.0, r.height)
        return min(1.0, total / page_area)
    except Exception:
        return 0.0


def extract_segments(doc: "pymupdf.Document", page_filter: set[int] | None,
                     translate_all: bool, tessdata_dir: str | None = None,
                     ocr_lang: str | None = "eng") -> list[Segment]:
    # 같은 왼쪽 시작 x좌표로 볼 수 있는 오차 허용치. 이보다 크게 벌어진 줄이 섞여 있으면
    # 표/다단 레이아웃으로 보고(pymupdf가 여러 셀/컬럼을 하나의 block으로 묶어버리는 경우가
    # 흔함) 블록 전체를 한 세그먼트로 합치지 않고 줄 단위로 쪼갠다. 안 쪼개면 서로 다른 셀의
    # 텍스트가 하나의 문단으로 합쳐져 번역 후 표 구조가 무너진다(칸이 비거나 내용이 뒤섞임).
    TABLE_LIKE_X0_TOLERANCE = 3.0

    def make_seg(pno: int, bno: int, text: str, bbox: tuple,
                sizes: list[float], colors: list[int], bold_votes: list[bool],
                line_boxes: list[tuple] | None, vertical: bool = False,
                is_ocr: bool = False) -> Segment | None:
        text = text.strip()
        if not text:
            return None
        if is_ocr:
            text = _sanitize_ocr_text(text, ocr_lang)
            if not text:
                return None
            plausible, ratio, reason = _ocr_script_plausibility(text, ocr_lang)
            if not plausible:
                print(f"  [OCR 필터] {pno + 1}페이지 블록 {bno}: 비정상 OCR 제외 "
                      f"(언어={ocr_lang}, 비율={ratio:.2f}, {reason}, 텍스트={text[:40]!r})")
                return None
            # 만화/삽화에서 OCR이 서로 다른 그림 영역의 글자를 하나의 줄로 잘못 합치면,
            # 그 합쳐진 bbox가 커지고 거기서 역산한 폰트 크기도 비정상적으로 커진다(정상
            # 말풍선 텍스트는 보통 8~16pt인데 20pt를 넘는 경우가 실제로 거의 다 이런 오탐
            # 이었음을 실제 만화 샘플로 확인함). 문자 자체는 CJK 스크립트라 위 검사를
            # 통과해버리므로 이 검사를 별도로 추가해 걸러낸다.
            est_size = Counter(sizes).most_common(1)[0][0] if sizes else 11.0
            if est_size > ocr_font_size_limit:
                print(f"  [OCR 필터] {pno + 1}페이지 블록 {bno}: 폰트 크기 이상치로 제외 "
                      f"(추정 {est_size:.1f}pt > 이 페이지 임계값 {ocr_font_size_limit:.1f}pt, "
                      f"텍스트={text[:40]!r})")
                return None
        return Segment(
            seg_id=f"page_{pno + 1:03d}_block_{bno:03d}",
            page=pno, bbox=bbox, text=text,
            font_size=(Counter(sizes).most_common(1)[0][0] if sizes else 11.0),
            color=f"#{(Counter(colors).most_common(1)[0][0] if colors else 0):06x}",
            bold=(sum(bold_votes) > len(bold_votes) / 2) if bold_votes else False,
            needs_translation=(True if translate_all else bool(LETTER_RE.search(text))),
            line_boxes=line_boxes,
            layout_boxes=[bbox],
            vertical=vertical,
            is_ocr=is_ocr,
        )

    def _is_vertical_dir(d: tuple) -> bool:
        """pymupdf line의 'dir'(방향 벡터)로 세로쓰기 여부 판정.
        가로쓰기는 보통 (1,0)에 가깝고, 세로쓰기(일본어 종서 등)나 회전된 텍스트는
        y성분이 지배적인 (0,±1)에 가깝다."""
        try:
            return abs(d[1]) > abs(d[0])
        except Exception:
            return False

    ocr_warned = {"done": False}  # tesseract 미설치 경고를 문서당 1번만 띄우기 위한 플래그
    resolved_tessdata = find_tessdata_dir(tessdata_dir)
    if tessdata_dir and not resolved_tessdata:
        print(f"[경고] 지정한 --tessdata-dir '{tessdata_dir}'에서 언어 데이터 파일"
              f"(*.traineddata)을 찾지 못함 - 자동 탐지도 실패했으므로 OCR이 동작하지 않을 수 있음")

    segments: list[Segment] = []
    for pno in range(doc.page_count):
        if page_filter is not None and pno not in page_filter:
            continue
        page = doc[pno]
        data = page.get_text("dict", sort=True)  # 위->아래, 왼->오른쪽 정렬
        is_ocr_page = False
        ocr_candidate_count = 0
        ocr_font_size_limit = OCR_MAX_PLAUSIBLE_FONT_SIZE  # OCR 성공시 페이지별로 동적 재계산됨
        page_seg_start = len(segments)  # 이 페이지 세그먼트가 시작되는 인덱스 (신뢰도 낮으면 롤백용)

        # 스캔본(이미지만 있고 텍스트 레이어가 없는 PDF) 자동 감지 + OCR 폴백.
        # 조건 1: 추출된 글자 수가 극히 적은데(20자 미만) 페이지에 이미지가 있으면 스캔본으로
        # 추정.
        # 조건 2: 이미 텍스트 레이어가 있어도(외부 OCR 도구가 미리 심어놓은 경우 포함) 그
        # 내용이 신뢰할 만한지 사전 검사한다 - 실제로 겪은 문제: 스캔본을 외부 OCR
        # 프로그램으로 먼저 인식시킨 뒤 이 도구에 넣으면, 텍스트 레이어가 있으니
        # total_chars>=20이 되어 우리 OCR 로직 자체가 전혀 발동을 안 하고, 그 쓰레기
        # 텍스트를 그대로 "원문"으로 신뢰해버렸다. 이미지가 페이지 대부분을 차지하는데
        # 언어가 지정돼 있으면, 기존 텍스트가 신뢰할 만한지 검사해서 낮으면 버리고 우리
        # 쪽 재-OCR(말풍선 검출 포함)로 대체한다.
        total_chars = sum(len(sp.get("text", "")) for b in data.get("blocks", [])
                          if b.get("type") == 0 for ln in b.get("lines", [])
                          for sp in ln.get("spans", []))
        existing_text_unreliable = False
        if total_chars >= 20:
            try:
                img_ratio = _estimate_image_area_ratio(page)
            except Exception:
                img_ratio = 0.0
            if img_ratio > 0.5:
                sample_texts = [sp.get("text", "") for b in data.get("blocks", [])
                                if b.get("type") == 0 for ln in b.get("lines", [])
                                for sp in ln.get("spans", [])]
                if ocr_lang is not None:
                    plausible_count = sum(1 for t in sample_texts
                                          if _ocr_script_plausibility(t, ocr_lang)[0])
                    reliability = plausible_count / max(1, len(sample_texts))
                    detail = f"기존 텍스트 신뢰율 {reliability:.0%}"
                else:
                    # 언어를 몰라 스크립트 매칭 검사를 못 할 때는 "텍스트 파편화 정도"로
                    # 이상을 감지한다: 정상 PDF 텍스트는 문장 단위로 긴 조각이 흔하지만,
                    # 오염된 외부 OCR 결과는 대부분 1~3자 이내로 심하게 쪼개져 있다(실제
                    # 확인된 패턴).
                    short_count = sum(1 for t in sample_texts if len(t.strip()) <= 3)
                    reliability = 1.0 - (short_count / max(1, len(sample_texts)))
                    detail = f"짧은 조각 비율 {1 - reliability:.0%} (언어 불명이라 파편화 정도로만 판단)"
                if reliability < 0.3:
                    existing_text_unreliable = True
                    print(f"[경고] {pno + 1}페이지: 이미 텍스트 레이어가 있지만 신뢰도가 "
                          f"낮습니다({detail}, 이미지 면적비율 {img_ratio:.0%}) - 외부 OCR "
                          f"도구가 심어놓은 오류일 가능성이 높습니다. 이 텍스트를 무시합니다.")

        if total_chars < 20 or existing_text_unreliable:
            try:
                has_images = bool(page.get_images())
            except Exception:
                has_images = False
            if has_images and ocr_lang is None:
                if not ocr_warned["done"]:
                    print(f"[정보] {pno + 1}페이지가 스캔본으로 보이지만 원문 언어를 알 수 없어 "
                          f"(자동 인식 상태) OCR을 시도하지 않았습니다. 언어를 모르는 채로 OCR을 "
                          f"강행하면 엉뚱한 언어팩으로 인식해 노이즈(쓰레기 텍스트)가 크게 늘어나는 "
                          f"문제가 있어 안전을 위해 건너뜁니다. 스캔본을 번역하려면 --source-lang으로 "
                          f"실제 언어를 지정하거나(예: Japanese, Korean) --ocr-lang을 직접 지정하세요.")
                    ocr_warned["done"] = True
                if existing_text_unreliable:
                    # 기존 텍스트를 못 믿겠다고 이미 판단했는데 언어를 몰라 재-OCR도 못 하는
                    # 상황 - 오염된 텍스트를 그대로 세그먼트로 만들면 안 되므로 비워서
                    # "텍스트 없음"으로 안전하게 처리한다(원본 이미지는 그대로 보존됨).
                    data = {"blocks": []}
            elif has_images:
                try:
                    # 1) 먼저 말풍선처럼 보이는 영역을 검출해 영역별로 개별 OCR을 시도한다
                    #    (전체 페이지 OCR보다 주변 그림의 간섭이 적어 노이즈가 줄어들 여지가
                    #    있음). 후보가 너무 적으면(일반 스캔 문서처럼 텍스트가 페이지 전체에
                    #    걸쳐 있는 경우 이 방식 자체가 안 맞음) 기존 전체 페이지 OCR로 폴백한다.
                    bubble_regions = _find_bubble_regions(page)
                    fake_blocks = []
                    if len(bubble_regions) >= 2:
                        fake_blocks = _build_fake_blocks_from_bubbles(
                            page, bubble_regions, ocr_lang, resolved_tessdata)

                    if fake_blocks:
                        ocr_data = {"blocks": fake_blocks}
                        mode_desc = f"말풍선 {len(fake_blocks)}개 영역 개별 인식"
                    else:
                        ocr_kwargs = {"flags": 0, "full": True, "dpi": 200, "language": ocr_lang}
                        if resolved_tessdata:
                            ocr_kwargs["tessdata"] = resolved_tessdata
                        ocr_textpage = page.get_textpage_ocr(**ocr_kwargs)
                        ocr_data = page.get_text("dict", textpage=ocr_textpage, sort=True)
                        mode_desc = "전체 페이지 인식"

                    ocr_chars = sum(len(sp.get("text", "")) for b in ocr_data.get("blocks", [])
                                    if b.get("type") == 0 for ln in b.get("lines", [])
                                    for sp in ln.get("spans", []))
                    ocr_candidate_count = sum(1 for b in ocr_data.get("blocks", [])
                                              if b.get("type") == 0 for ln in b.get("lines", [])
                                              if ln.get("spans"))
                    # 기존 텍스트가 신뢰 불가 판정을 받은 경우엔 글자수 비교 없이 무조건
                    # 새 OCR 결과로 교체한다(기존 total_chars는 이미 "믿을 수 없는 값"이므로
                    # 비교 기준으로 삼는 게 무의미함).
                    if existing_text_unreliable or ocr_chars > total_chars:
                        data = ocr_data
                        is_ocr_page = True
                        # 이 페이지 OCR 후보들의 폰트 크기 분포로 동적 이상치 임계값을 정한다.
                        # 절대값 하나로 고정하면(예: 20pt) 딜레마가 생긴다 - 만화 말풍선은
                        # 보통 8~16pt라 20pt짜리 오탐도 잡아야 하지만, 일반 스캔 문서는 24pt
                        # 제목이 완전히 정상일 수 있어 그러면 정상 텍스트까지 걸러진다(실제
                        # 회귀 테스트에서 확인된 문제). 그래서 "이 페이지 대부분의 글자 크기"
                        # 대비 상대적으로 튀는 것만 이상치로 본다 - 문서마다 스스로 기준을 잡는다.
                        all_sizes = [round(float(sp.get("size", 11.0)), 1)
                                    for b in ocr_data.get("blocks", []) if b.get("type") == 0
                                    for ln in b.get("lines", []) for sp in ln.get("spans", [])]
                        if all_sizes:
                            all_sizes.sort()
                            median_size = all_sizes[len(all_sizes) // 2]
                            ocr_font_size_limit = max(OCR_MAX_PLAUSIBLE_FONT_SIZE, median_size * 2.2)
                        print(f"[정보] {pno + 1}페이지: 텍스트 레이어가 거의 없거나 신뢰 불가 "
                              f"-> OCR로 텍스트 {ocr_chars}자 추출 ({mode_desc})"
                              + (f" (tessdata: {resolved_tessdata})" if resolved_tessdata else ""))
                except Exception as e:
                    if not ocr_warned["done"]:
                        print(f"[경고] {pno + 1}페이지가 스캔본으로 보이지만 OCR을 사용할 수 없습니다: {e}")
                        if resolved_tessdata:
                            print(f"       tessdata 경로는 찾음({resolved_tessdata})인데도 실패했습니다. "
                                  f"Tesseract 실행파일 자체나 언어 데이터가 손상됐을 수 있습니다.")
                        else:
                            print("       Tesseract의 언어 데이터(tessdata) 폴더를 자동으로 찾지 못했습니다. "
                                  "Tesseract가 설치돼 있다면 --tessdata-dir로 직접 경로를 지정하세요 "
                                  "(보통 Windows는 'C:\\Program Files\\Tesseract-OCR\\tessdata'). "
                                  "설치가 안 돼 있다면 https://github.com/tesseract-ocr/tesseract 에서 설치하세요.")
                        print("       (OCR을 못 쓰면 이 페이지는 원문 없이 빈 상태로 남습니다.)")
                        ocr_warned["done"] = True
                        ocr_warned["done"] = True

        bno = 0
        block_idx = 0
        ocr_block_candidates: dict[int, int] = {}
        ocr_seg_indices_by_block: dict[int, list[int]] = {}
        ocr_block_is_table: dict[int, bool] = {}
        for block in data.get("blocks", []):
            if block.get("type") != 0:           # 0 = 텍스트 블록
                continue
            if is_ocr_page:
                ocr_block_candidates[block_idx] = len(block.get("lines", []))

            line_infos = []  # 각 줄: (text, bbox, sizes, colors, bold_votes, is_vertical)
            for line in block.get("lines", []):
                line_text = "".join(sp.get("text", "") for sp in line.get("spans", []))
                if not line_text.strip():
                    continue
                l_sizes, l_colors, l_bold = [], [], []
                for sp in line.get("spans", []):
                    l_sizes.append(round(float(sp.get("size", 11.0)), 1))
                    l_colors.append(int(sp.get("color", 0)))
                    l_bold.append(bool(sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower())
                is_vert = _is_vertical_dir(line.get("dir", (1, 0)))
                line_infos.append((line_text, tuple(line.get("bbox", block["bbox"])),
                                   l_sizes, l_colors, l_bold, is_vert))
            if not line_infos:
                continue

            # 블록 전체의 세로쓰기 여부: 과반수 줄이 세로 방향이면 세로쓰기 블록으로 판정
            # 블록 전체의 세로쓰기 여부 판정: 두 가지 근거를 함께 본다.
            #   1) dir 벡터 기반: 과반수 줄의 방향 벡터가 세로(y성분 지배적)
            #   2) 위치 퍼짐 기반: 줄들이 세로로 넓게 퍼져 있고(y_range) 가로로는 좁게
            #      모여있으면(x_range) 세로쓰기로 본다.
            #   1)만으로는 부족하다 - 세로쓰기 문서에서 한 글자만 있는 줄(흔한 경우, 특히
            #   영숫자나 조사 한 글자)은 pymupdf가 dir을 방향성 없는 기본값 (1,0)으로 보고할
            #   때가 있어서, 실제로는 세로로 죽 이어지는 문단인데도 dir만 보면 전부 가로로
            #   오판될 수 있다(그러면 회전 없이 삽입되어 좁고 긴 bbox에 텍스트가 가로로
            #   욱여넣어지며 심하게 깨진다). 위치 퍼짐을 보조 근거로 추가해 이를 보완한다.
            vertical_by_dir = sum(1 for li in line_infos if li[5]) > len(line_infos) / 2
            if len(line_infos) > 1:
                y0s_all = [li[1][1] for li in line_infos]
                x0s_all = [li[1][0] for li in line_infos]
                y_spread = max(y0s_all) - min(y0s_all)
                x_spread = max(x0s_all) - min(x0s_all)
                avg_line_len = sum(len(li[0]) for li in line_infos) / len(line_infos)
                # 왼쪽 정렬된 일반 가로쓰기 문단도 "x0가 거의 같고 y0가 줄마다 증가"하는
                # 구조라 y_spread > x_spread만으로는 세로쓰기와 구분이 안 된다(오탐 발생 확인됨:
                # 여러 줄짜리 영문 문단이 전부 세로쓰기로 오판됨). 결정적 차이는 줄 하나에
                # 담긴 글자 수다 - 세로쓰기 컬럼은 줄마다 한자/가나/문자 1~3개 정도인 반면,
                # 가로쓰기 문단은 줄마다 단어 여러 개(보통 수십 자)가 들어간다. 그래서 줄이
                # 충분히 짧을 때만 위치 퍼짐 판정을 적용한다.
                short_lines = avg_line_len <= 4.0
                vertical_by_spread = short_lines and y_spread > x_spread * 1.5
            else:
                vertical_by_spread = False
            block_vertical = vertical_by_dir or vertical_by_spread

            if block_vertical:
                # 세로쓰기(일본어 종서 등) 판정은 두 단계다.
                #
                # 1) 같은 컬럼(x0가 거의 동일)인 줄들 = 한 세로줄 안에서 위->아래로 이어지는
                #    글자/단어들이다. 이건 무조건 하나의 문단이다 - 순서대로 아래로 내려가니
                #    y-range가 서로 안 겹치는 게 정상인데, 이걸 "안 겹치니 표"로 오판하면
                #    (실제로 이전 버전의 버그) 한 문장이 낱글자 단위로 쪼개지고, 그 결과 각
                #    조각의 bbox 높이가 번역문 길이에 비해 턱없이 부족해져서 극단적으로
                #    축소되며 "2줄로 들어가고 그 안에서 글자가 가로쓰기처럼 나열되는" 현상이
                #    생긴다(폭 안에 여러 글자가 들어갈 만큼 폰트가 작아지면서 세로 1글자당
                #    1줄 원칙이 깨짐).
                # 2) 서로 다른 컬럼(x0가 다름)이 섞여 있을 때만 표/문단을 구분해야 하는데,
                #    이때는 y-range 겹침으로 판단한다: 겹치면 같은 문단의 여러 컬럼(오른쪽에서
                #    왼쪽으로 읽는 구조), 안 겹치면 표의 다른 행일 가능성이 높다.
                x0s = [li[1][0] for li in line_infos]
                if (max(x0s) - min(x0s)) <= TABLE_LIKE_X0_TOLERANCE:
                    table_like = False
                else:
                    def _y_overlap_ratio(a, b):
                        top, bot = max(a[0], b[0]), min(a[1], b[1])
                        if bot <= top:
                            return 0.0
                        return (bot - top) / max(min(a[1] - a[0], b[1] - b[0]), 1e-6)

                    y_ranges = [(li[1][1], li[1][3]) for li in line_infos]
                    ref = y_ranges[0]
                    table_like = any(_y_overlap_ratio(ref, r) < 0.3 for r in y_ranges[1:])
            else:
                x0s = [li[1][0] for li in line_infos]
                # OCR 결과는 좌표가 원본 PDF 텍스트보다 부정확하다(픽셀->포인트 변환,
                # tesseract 인식 오차 등으로 왼쪽 정렬이어야 할 줄들의 x0가 몇~수십 포인트
                # 어긋나는 게 흔함). 고정된 좁은 허용치를 그대로 쓰면 정상적인 가로쓰기
                # 문단이 "표"로 오판되어 줄 단위로 쪼개지고, 그중 애매한 한 조각(예: 기호
                # 하나)이 신뢰도 검사에 실패하면 나머지 멀쩡한 줄들까지 함께 제거되는 문제가
                # 실제로 있었다. OCR 유래 블록은 허용치를 훨씬 관대하게 적용한다.
                tolerance = TABLE_LIKE_X0_TOLERANCE * 8 if is_ocr_page else TABLE_LIKE_X0_TOLERANCE
                table_like = (max(x0s) - min(x0s)) > tolerance

            if not table_like:
                if is_ocr_page:
                    ocr_block_is_table[block_idx] = False
                # 일반 문단: 지금까지처럼 블록 전체를 한 세그먼트로 (문맥 유지, 번역 품질 우선)
                text = "\n".join(li[0] for li in line_infos)
                sizes = [s for li in line_infos for s in li[2]]
                colors = [c for li in line_infos for c in li[3]]
                bold_votes = [b for li in line_infos for b in li[4]]
                line_boxes = [li[1] for li in line_infos]
                seg = make_seg(pno, bno, text, tuple(block["bbox"]), sizes, colors,
                              bold_votes, line_boxes, vertical=block_vertical, is_ocr=is_ocr_page)
                if seg:
                    segments.append(seg)
                    if is_ocr_page:
                        ocr_seg_indices_by_block.setdefault(block_idx, []).append(len(segments) - 1)
                    bno += 1
            else:
                if is_ocr_page:
                    ocr_block_is_table[block_idx] = True
                # 표/다단 의심(줄마다 시작 x가 다름): 각 줄을 독립 세그먼트로 분리해
                # 각자 원래 위치(그 줄의 bbox)에 개별 번역·배치한다. 셀 경계가 깨지지 않는다.
                for text, bbox, sizes, colors, bold_votes, is_vert in line_infos:
                    seg = make_seg(pno, bno, text, bbox, sizes, colors, bold_votes, [bbox],
                                  vertical=is_vert, is_ocr=is_ocr_page)
                    if seg:
                        segments.append(seg)
                        if is_ocr_page:
                            ocr_seg_indices_by_block.setdefault(block_idx, []).append(len(segments) - 1)
                        bno += 1
            if is_ocr_page:
                block_idx += 1

        # OCR 신뢰도 검사: 문자 단위 필터(스크립트 검사, 폰트 크기)를 다 거치고도 "형식은
        # 멀쩡하지만 의미 없는 글자 조합"은 규칙 기반으로 걸러낼 수 없다(예: 그림을 오독해
        # 만든 진짜 같은 한자 나열). 페이지 전체를 뭉뚱그려 "통과율이 낮으면 전부 폐기"하면
        # 안전하지만, "말풍선 일부(가로쓰기)는 정확히 인식됐고 다른 일부(세로쓰기)만 실패"
        # 하는 흔한 경우에도 정상 인식된 부분까지 다 버려지는 문제가 있었다(실제 확인:
        # 6개 말풍선 중 3개는 완벽히 인식됐는데 나머지가 실패하자 전부 폐기됨). 그래서
        # 페이지 전체가 아니라 "말풍선(블록) 단위"로 개별 신뢰도를 판단한다.
        #
        # 다만 이렇게만 하면 새로운 위험이 생긴다: 후보 수가 적은 블록은 "우연히" 30%
        # 임계값을 넘기 쉽다(예: 후보 3개 중 1개만 맞아도 33%). 실제로 만화 페이지에서
        # 이 허점으로 노이즈 4개가 새어나와 그림 위에 쓰레기 텍스트가 그려지는 회귀가
        # 발생했다. 그래서 후보 수가 적은 블록일수록 훨씬 엄격한 기준(사실상 전원 합격)을
        # 요구한다 - 표본이 작을수록 통계적으로 우연한 통과가 쉬워지기 때문이다.
        if is_ocr_page and ocr_block_candidates:
            to_remove = set()
            for b_idx, cand_count in ocr_block_candidates.items():
                if cand_count <= 0:
                    continue
                seg_indices = ocr_seg_indices_by_block.get(b_idx, [])
                if not ocr_block_is_table.get(b_idx, False):
                    # 문단으로 합쳐진 블록: 세그먼트 하나 = 문단 전체이므로 "줄 수 대비
                    # 세그먼트 수" 비율은 애초에 의미가 없다(항상 1/N 꼴이 되어 통과율이
                    # 실제와 무관하게 낮아 보이는 착시가 생김 - 실제로 이 계산 오류 때문에
                    # 멀쩡히 인식된 문단이 통째로 폐기되는 버그가 있었다). 이미 make_seg의
                    # 개별 스크립트 신뢰도 검사를 통과했다면 그걸로 충분하다.
                    continue
                block_pass_rate = len(seg_indices) / cand_count
                required_rate = 0.5 if cand_count <= 4 else 0.3
                if block_pass_rate < required_rate:
                    to_remove.update(seg_indices)
            if to_remove:
                removed_texts = [segments[i].text[:20] for i in sorted(to_remove)]
                print(f"[경고] {pno + 1}페이지: 일부 OCR 결과의 신뢰도가 너무 낮아 "
                      f"{len(to_remove)}개 영역을 개별 폐기했습니다(그림을 오인식했을 "
                      f"가능성 높음, 원본 그대로 둠): {removed_texts}")
                segments = [s for i, s in enumerate(segments) if i not in to_remove]
    return segments

_SENTENCE_END_RE = re.compile(r'[.!?:;"\u201d\u2026。！？」』]\s*$')


def _y_overlap_ratio(a: tuple, b: tuple) -> float:
    top, bot = max(a[1], b[1]), min(a[3], b[3])
    if bot <= top:
        return 0.0
    return (bot - top) / max(min(a[3] - a[1], b[3] - b[1]), 1e-6)


def _x_gap(a: tuple, b: tuple) -> float:
    """두 bbox가 수평으로 겹치지 않을 때의 간격(pt). 겹치면 0."""
    if a[2] <= b[0]:
        return b[0] - a[2]
    if b[2] <= a[0]:
        return a[0] - b[2]
    return 0.0


def _rects_overlap_area(a: tuple, b: tuple) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _y_gap(a: tuple, b: tuple) -> float:
    """두 bbox가 세로로 겹치지 않을 때의 간격(pt). 겹치면 0."""
    if a[3] <= b[1]:
        return b[1] - a[3]
    if b[3] <= a[1]:
        return a[1] - b[3]
    return 0.0


def _can_merge_vertical(a: Segment, b: Segment) -> bool:
    """
    세로쓰기 세그먼트 둘이 같은 문장의 조각(병합해야 함)인지 판정한다. 두 가지 패턴이
    있다: (1) 다른 컬럼이 나란히 있는 경우(여러 세로줄로 구성된 한 문장), (2) 같은
    컬럼인데 위아래로 이어지는 경우(한 세로줄이 중간에 별도 블록으로 쪼개짐 - 실제
    샘플에서 확인됨, 예: "한글로"와 "변역된경"이 같은 x에서 y만 이어짐).

    중요한 예외: 두 세그먼트가 이미 각자 "완결된 독립 문장"이면 절대 합치지 않는다.
    실제로 서로 다른 9개의 완전한 문장이 나란한 컬럼으로 배치된 문서에서, 이 예외가
    없으면 전부 하나로 잘못 합쳐지는 심각한 회귀가 발생했다(각 문장이 이미 그 자체로
    완결돼 있다면 옆 컬럼과 이어질 이유가 없다 - 문장부호로 끝나는지로 판별한다).
    """
    max_fs = max(a.font_size, b.font_size, 1.0)
    min_fs = max(min(a.font_size, b.font_size), 0.1)
    if max_fs / min_fs > 1.15:
        return False  # 폰트 크기가 다르면(제목 vs 본문 등 서로 다른 요소) 절대 병합 안 함
    x_gap = _x_gap(a.bbox, b.bbox)
    same_column = x_gap <= max_fs * 0.5

    if same_column:
        first, second = (a, b) if a.bbox[1] <= b.bbox[1] else (b, a)
    else:
        first, second = (a, b) if a.bbox[0] >= b.bbox[0] else (b, a)
    if _SENTENCE_END_RE.search(first.text.strip()):
        return False  # 먼저 읽는 쪽이 이미 완결된 문장 -> 이어붙이지 않음

    if same_column:
        return _y_gap(a.bbox, b.bbox) <= max_fs * 1.5
    if x_gap > max_fs * 2.5:  # 컬럼 간격이 글자 크기 대비 너무 멀면 다른 말풍선일 가능성
        return False
    return _y_overlap_ratio(a.bbox, b.bbox) > 0.15


def _merge_two_vertical(a: Segment, b: Segment) -> Segment:
    """
    같은 컬럼(위아래로 이어짐)이면 위(y가 작은 쪽)가 먼저, 다른 컬럼(나란히 있음)이면
    오른쪽(x가 큰 쪽)이 먼저 오도록 텍스트를 결합한다(세로쓰기 전통적 읽기 순서).
    """
    max_fs = max(a.font_size, b.font_size, 1.0)
    same_column = _x_gap(a.bbox, b.bbox) <= max_fs * 0.5
    if same_column:
        first, second = (a, b) if a.bbox[1] <= b.bbox[1] else (b, a)
    else:
        first, second = (a, b) if a.bbox[0] >= b.bbox[0] else (b, a)
    text = first.text + "\n" + second.text
    bbox = (min(a.bbox[0], b.bbox[0]), min(a.bbox[1], b.bbox[1]),
            max(a.bbox[2], b.bbox[2]), max(a.bbox[3], b.bbox[3]))
    base = first if len(first.text) >= len(second.text) else second
    return Segment(
        seg_id=base.seg_id, page=base.page, bbox=bbox, text=text,
        font_size=base.font_size, color=base.color, bold=base.bold,
        needs_translation=(a.needs_translation or b.needs_translation),
        line_boxes=(a.line_boxes or []) + (b.line_boxes or []),
        layout_boxes=(a.layout_boxes or []) + (b.layout_boxes or []),
        vertical=True, is_ocr=(a.is_ocr or b.is_ocr),
    )


def merge_adjacent_segments(segments: list[Segment]) -> list[Segment]:
    """
    세로쓰기 문서에서 pymupdf가 한 문장(말풍선)의 서로 다른 컬럼을 별도의 물리 블록으로
    나눠 제공하는 경우가 실제로 확인됐다(세로쓰기 샘플 테스트에서 한 문장이 블록 내부
    줄 병합만으로는 합쳐지지 않는 3~4개의 완전히 별개인 블록으로 쪼개짐 - "블록 안의
    줄들"만 다루는 기존 로직으로는 애초에 손댈 수 없는 범위였음).

    그래서 세로쓰기 세그먼트(vertical=True)에 한해서만 "인접한 다른 블록"끼리도
    병합한다. 가로쓰기 문서(표/목차 등)는 이 병합이 위험할 수 있어(내용이 섞이거나
    위치가 틀어짐 - 예전에 이 문제로 "물리 블록 절대 병합 안 함" 정책이 생겼었음)
    전혀 건드리지 않는다. vertical=True인 것만 대상으로 삼는 게 핵심 안전장치다:
    가로쓰기 표/문단은 원래도 병합 없이 세그먼트 단위가 정확했으므로 회귀 없음.

    병합 조건: 같은 페이지, 컬럼 간격이 글자 크기 대비 가깝고, 세로 범위가 겹칠 것.
    병합 후에도 이 페이지의 다른 세그먼트와 겹치지 않는지 확인해 안전하게 처리한다.
    """
    by_page: dict[int, list[int]] = defaultdict(list)
    for i, s in enumerate(segments):
        by_page[s.page].append(i)

    result: dict[int, Segment] = {i: s for i, s in enumerate(segments)}
    merged_away: set[int] = set()

    for pno, idxs in by_page.items():
        vert_idxs = [i for i in idxs if segments[i].vertical]
        if not vert_idxs:
            continue
        # 세로쓰기 세그먼트뿐 아니라, 텍스트가 매우 짧은(<=3자) 세그먼트도 후보 풀에
        # 넣는다 - 단일 글자짜리 블록은 dir 벡터나 위치 퍼짐만으로는 세로쓰기 여부를
        # 판정할 근거 자체가 없어(줄이 하나뿐이라) vertical=False로 잘못 판정되는 경우가
        # 실제로 있었다(예: "우" 한 글자). 이런 미아 블록이 확실한 세로쓰기 이웃과 병합
        # 조건을 만족하면 흡수되게 한다. 단, 병합 성립에는 최소 한쪽이 vertical=True여야
        # 한다는 조건(아래)을 반드시 걸어 가로쓰기 라벨 등이 잘못 섞이는 걸 막는다.
        short_idxs = [i for i in idxs if not segments[i].vertical
                     and len(segments[i].text.strip()) <= 3]
        candidate_idxs = set(vert_idxs) | set(short_idxs)

        changed = True
        while changed:
            changed = False
            active = [i for i in candidate_idxs if i not in merged_away]
            # 정렬 순서상 "바로 옆"이라는 것만으로는 인접 여부를 보장 못 한다(같은 페이지에
            # 서로 다른 말풍선이 여럿 있으면, x값이 우연히 비슷해 순서상 나란해도 실제로는
            # 전혀 다른 말풍선일 수 있음 - 실제로 이 문제로 병합이 잘못 건너뛴 사례 확인).
            # 그래서 "실제 병합 조건(_can_merge_vertical)을 만족하는 모든 쌍" 중 간격이
            # 가장 작은(가장 가까운) 것부터 매 라운드 하나씩 병합한다.
            best = None  # (gap_score, ia, ib, candidate)
            for pi in range(len(active)):
                for pj in range(pi + 1, len(active)):
                    ia, ib = active[pi], active[pj]
                    seg_a, seg_b = result[ia], result[ib]
                    if not (seg_a.vertical or seg_b.vertical):
                        continue  # 최소 한쪽은 세로쓰기여야 병합 허용 (안전장치)
                    if not _can_merge_vertical(seg_a, seg_b):
                        continue
                    candidate = _merge_two_vertical(seg_a, seg_b)
                    others = [result[k] for k in idxs
                             if k not in (ia, ib) and k not in merged_away]
                    if any(_rects_overlap_area(candidate.bbox, o.bbox) > 0 for o in others):
                        continue  # 다른 세그먼트를 침범하면 이 병합은 포기(안전 우선)
                    gap_score = _x_gap(seg_a.bbox, seg_b.bbox) + _y_gap(seg_a.bbox, seg_b.bbox)
                    if best is None or gap_score < best[0]:
                        best = (gap_score, ia, ib, candidate)
            if best is not None:
                _, ia, ib, candidate = best
                result[ia] = candidate
                merged_away.add(ib)
                changed = True

    return [result[i] for i in range(len(segments)) if i not in merged_away]