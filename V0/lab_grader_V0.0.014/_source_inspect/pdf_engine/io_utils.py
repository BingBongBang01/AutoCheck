"""용어집 로드, 페이지 범위 파싱, 번역 결과 내보내기/가져오기(JSON)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .segment import Segment

def load_glossary(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        sys.exit(f"[오류] 용어집 파일이 없습니다: {path}")
    lines: list[str] = []
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.items() if isinstance(data, dict) else \
            ((d.get("source", ""), d.get("target", "")) for d in data)
        for src, dst in items:
            if src:
                lines.append(f"{src} => {dst}")
    else:  # csv/txt: "source,target" 또는 "source => target"
        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            if "=>" in raw:
                src, dst = raw.split("=>", 1)
            elif "," in raw:
                src, dst = raw.split(",", 1)
            else:
                continue
            lines.append(f"{src.strip()} => {dst.strip()}")
    return "\n".join(lines)


def parse_pages(spec: str | None, page_count: int) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a) - 1, int(b)))
        else:
            out.add(int(part) - 1)
    return {p for p in out if 0 <= p < page_count}


def export_translations(path: str, segments: list[Segment]) -> None:
    payload = {"segments": [
        {"segment_id": s.seg_id, "page": s.page + 1, "source": s.text,
         "translated": s.translated}
        for s in segments if s.needs_translation
    ]}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[i] 번역 결과 JSON 저장: {path}")


def import_translations(path: str, segments: list[Segment]) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    table = {d["segment_id"]: d.get("translated") for d in data.get("segments", [])}
    hit = 0
    for s in segments:
        if s.needs_translation and table.get(s.seg_id):
            s.translated = table[s.seg_id]
            hit += 1
    print(f"[i] JSON에서 {hit}개 세그먼트 번역 로드: {path}")