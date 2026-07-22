"""세그먼트를 배치로 묶고, LLM에 보낼 사용자 프롬프트를 구성한다."""
from __future__ import annotations

import re

from .segment import Segment

def make_batches(segments: list[Segment], max_chars: int, max_segs: int):
    batch, size = [], 0
    for s in segments:
        if not s.needs_translation:
            continue
        if batch and (size + len(s.text) > max_chars or len(batch) >= max_segs):
            yield batch
            batch, size = [], 0
        batch.append(s)
        size += len(s.text)
    if batch:
        yield batch


def render_segments_block(batch: list[Segment]) -> str:
    parts = [f"SEGMENT_ID: {s.seg_id}\nTEXT:\n{s.text}" for s in batch]
    return "\n\n-----\n\n".join(parts)


def render_prev_context(pairs: list[tuple[str, str]], limit_chars: int = 1500) -> str:
    """직전 번역 (원문, 번역) 쌍을 용어 일관성 유지용으로 렌더링."""
    if not pairs:
        return "(none)"
    chunks, total = [], 0
    for src, dst in reversed(pairs):
        chunk = f"SOURCE: {src}\nTRANSLATION: {dst}"
        if total + len(chunk) > limit_chars and chunks:
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n---\n".join(reversed(chunks))


_AUTO_DETECT_TOKENS = {"auto", "auto-detect", "autodetect", "자동", "자동 인식", "자동인식"}


def resolve_source_lang(raw: str) -> str:
    """
    GUI의 '자동 인식' 선택(또는 --source-lang auto)을 모델이 이해할 자연어 지시문으로 치환.
    일반 언어명(예: English, 한국어)은 그대로 통과시킨다.
    """
    if raw.strip().lower() in _AUTO_DETECT_TOKENS:
        return "the original language of the text (detect it automatically per segment)"
    return raw


def build_user_prompt(template: str, args, glossary_text: str,
                      prev_context: str, batch: list[Segment]) -> str:
    repl = {
        "{{source_language}}": resolve_source_lang(args.source_lang),
        "{{target_language}}": args.target_lang,
        "{{document_type}}": args.doc_type,
        "{{translation_style}}": args.style,
        "{{terminology_policy}}": args.terminology_policy,
        "{{glossary_data}}": glossary_text or "(no glossary provided)",
        "{{document_title}}": args.title,
        "{{document_domain}}": args.domain,
        "{{document_instructions}}": args.instructions or "(none)",
        "{{prev_context}}": prev_context,
        "{{text_segments}}": render_segments_block(batch),
    }
    out = template
    for key, val in repl.items():
        out = out.replace(key, val)
    return out