"""
이어서-번역을 위한 파일명 규칙(완료/미완료 페이지 구간 기록), 구간이 많을 때의
압축 표기(-MULTIn), 정확한 페이지 목록을 담는 사이드카 진행정보(.progress.json).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_RANGE_TOKEN = r"\d{3}-\d{3}(?:-MULTI\d+)?"
RESUME_FILENAME_RE = re.compile(
    rf"^(?P<base>.+?)_(?:translated|T)_(?P<tranges>{_RANGE_TOKEN}(?:_{_RANGE_TOKEN})*)"
    rf"_(?:untranslated|unT)_(?P<uranges>{_RANGE_TOKEN}(?:_{_RANGE_TOKEN})*)$"
)
# 파일명에 구간을 직접 나열하는 대신 압축(MULTI) 표기로 전환하는 기준
_COMPACT_RANGE_COUNT_THRESHOLD = 5   # 구간 수가 이보다 많으면 압축
_COMPACT_STEM_LENGTH_THRESHOLD = 140  # 풀어쓴 stem 길이가 이보다 길면 압축


def collapse_to_ranges(pages: list[int]) -> list[tuple[int, int]]:
    """[3,4,5,10,11,20] -> [(3,5),(10,11),(20,20)] 처럼 연속 페이지를 구간으로 묶는다."""
    if not pages:
        return []
    pages = sorted(set(pages))
    ranges: list[tuple[int, int]] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append((start, prev))
            start = prev = p
    ranges.append((start, prev))
    return ranges


def _parse_range_tokens(s: str) -> tuple[list[tuple[int, int]], bool]:
    """'010-020_030-034' 또는 압축형 '010-090-MULTI12'를 파싱. (구간목록, 압축여부)"""
    ranges: list[tuple[int, int]] = []
    is_compact = False
    for part in s.split("_"):
        m = re.match(r"(\d{3})-(\d{3})(?:-MULTI(\d+))?$", part)
        if not m:
            continue
        ranges.append((int(m.group(1)), int(m.group(2))))
        if m.group(3) is not None:
            is_compact = True
    return ranges, is_compact


def parse_resume_filename(stem: str) -> dict | None:
    """
    '<base>_translated_###-@@@_untranslated_$$$-%%%[_...]' (축약형 _T_/_unT_, 압축형 -MULTIn 포함)
    패턴을 파일명에서 감지한다. 압축형(MULTIn)이면 정확한 구간을 파일명만으로는 복원할 수 없으므로
    u_ranges/t_ranges를 None으로 반환해 호출측이 사이드카 JSON을 찾도록 신호한다.
    """
    m = RESUME_FILENAME_RE.match(stem)
    if not m:
        return None
    t_ranges, t_compact = _parse_range_tokens(m.group("tranges"))
    u_ranges, u_compact = _parse_range_tokens(m.group("uranges"))
    if t_ranges == [(0, 0)]:
        t_ranges = []
    if u_ranges == [(0, 0)]:
        u_ranges = []
    unresolved = t_compact or u_compact
    return {
        "base": m.group("base"),
        "t_ranges": None if unresolved else t_ranges,
        "u_ranges": None if unresolved else u_ranges,
        "t_start": t_ranges[0][0] if t_ranges else 0,
        "t_end": t_ranges[-1][1] if t_ranges else 0,
        "unresolved": unresolved,
    }


def sidecar_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + ".progress.json")


def write_progress_sidecar(out_path: Path, base_stem: str,
                           t_ranges: list[tuple[int, int]],
                           u_ranges: list[tuple[int, int]],
                           doc_page_count: int) -> None:
    """
    출력 PDF 옆에 정확한 페이지 진행정보를 JSON으로 남긴다. 파일명이 압축형(MULTIn)으로
    표시된 경우에도 이 파일이 있으면 이어서-번역 시 정확한 구간을 그대로 복원할 수 있다.
    파일명을 사람이 옮기거나 사이드카만 지워도, 파일명이 비압축형이면 파일명만으로 복원 가능.
    """
    data = {
        "base": base_stem,
        "t_ranges": [list(r) for r in t_ranges],
        "u_ranges": [list(r) for r in u_ranges],
        "doc_page_count": doc_page_count,
    }
    try:
        sidecar_path_for(out_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[경고] 진행정보 사이드카(.progress.json) 저장 실패(무시하고 계속): {e}")


def load_resume_info(in_path: Path) -> dict | None:
    """
    사이드카 JSON을 우선 사용하고(가장 정확), 없으면 파일명에서 파싱한다.
    파일명이 압축형(MULTIn)인데 사이드카가 없으면 정확한 구간 복원이 불가하므로 경고 후 None.
    """
    sc = sidecar_path_for(in_path)
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
            t_ranges = [tuple(r) for r in data["t_ranges"]]
            u_ranges = [tuple(r) for r in data["u_ranges"]]
            return {
                "base": data["base"], "t_ranges": t_ranges, "u_ranges": u_ranges,
                "t_start": t_ranges[0][0] if t_ranges else 0,
                "t_end": t_ranges[-1][1] if t_ranges else 0,
                "unresolved": False, "from_sidecar": True,
            }
        except Exception as e:
            print(f"[경고] 사이드카 진행정보(.progress.json) 손상, 무시하고 파일명으로 시도: {e}")
    info = parse_resume_filename(in_path.stem)
    if info and info.get("unresolved"):
        print("[경고] 파일명이 압축 형식(MULTIn)인데 사이드카(.progress.json)를 찾지 못해 "
              "정확한 이어서-번역이 불가합니다. 이 파일은 새 문서로 처리합니다. "
              "(사이드카 파일을 원본 PDF와 같은 폴더에 그대로 둬야 합니다)")
        return None
    return info


def build_output_stem(base_stem: str, t_ranges: list[tuple[int, int]],
                      u_ranges: list[tuple[int, int]]) -> tuple[str, bool]:
    """
    번역 완료/미완료 페이지 집합을 파일명에 기록한다.
    구간 수가 많거나(> 5) 풀어쓴 길이가 길면(> 140자) 압축형(-MULTIn)으로 전환하고,
    정확한 구간은 사이드카(.progress.json)에 저장하도록 True를 반환한다.
    반환값: (파일명 stem, 사이드카 필요 여부)
    """
    fmt = lambda n: f"{n:03d}"

    def full_part(tag: str, ranges: list[tuple[int, int]]) -> str:
        disp = ranges if ranges else [(0, 0)]
        return f"_{tag}_" + "_".join(f"{fmt(a)}-{fmt(b)}" for a, b in disp)

    def compact_part(tag: str, ranges: list[tuple[int, int]]) -> str:
        if not ranges:
            return f"_{tag}_000-000"
        lo, hi = min(a for a, _ in ranges), max(b for _, b in ranges)
        return f"_{tag}_{fmt(lo)}-{fmt(hi)}-MULTI{len(ranges)}"

    needs_compact = len(t_ranges) > _COMPACT_RANGE_COUNT_THRESHOLD or \
        len(u_ranges) > _COMPACT_RANGE_COUNT_THRESHOLD
    if not needs_compact:
        stem = base_stem + full_part("translated", t_ranges) + full_part("untranslated", u_ranges)
        if len(stem) <= _COMPACT_STEM_LENGTH_THRESHOLD:
            return stem, False
        # 구간 수는 적은데 base_stem 자체가 길어서 넘친 경우 -> 축약 태그(_T_/_unT_)만 우선 시도
        short_stem = base_stem + full_part("translated", t_ranges).replace("_translated", "_T") + \
            full_part("untranslated", u_ranges).replace("_untranslated", "_unT")
        if len(short_stem) <= _COMPACT_STEM_LENGTH_THRESHOLD + 20:
            return short_stem, False

    stem = base_stem + compact_part("translated", t_ranges) + compact_part("untranslated", u_ranges)
    if len(stem) > 200:
        # base_stem 자체가 극단적으로 긴 경우: 해시로 축약(사이드카 JSON이 유일한 진실 소스가 됨)
        h = hashlib.sha1(base_stem.encode("utf-8")).hexdigest()[:8]
        stem = f"{base_stem[:60]}_{h}" + \
            compact_part("translated", t_ranges) + compact_part("untranslated", u_ranges)
    return stem, True