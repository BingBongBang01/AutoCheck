#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_pdf.py — AI(Anthropic Claude/Gemini/OpenAI/로컬 NPU-GPU) 기반 PDF 번역기.

이 파일은 CLI 진입점(파사드)이다. 실제 구현은 기능별로 pdf_engine/ 패키지에
모듈화되어 있고, 이 파일은 (1) argparse/main() 진입점, (2) 하위 호환을 위해
`from translate_pdf import extract_segments` 같은 기존 참조가 계속 동작하도록
pdf_engine의 심볼을 재노출하는 역할만 한다.

파이프라인 (데이터 흐름):
  input.pdf
   -> [1] 추출   : pdf_engine.extraction - PyMuPDF get_text("dict")로 텍스트 블록별
                   (내용, bbox 좌표, 폰트 크기, 색, 굵기) 추출. 스캔본은 OCR 자동 폴백.
   -> [2] 배치   : pdf_engine.batching - segment_id 부여 후 문자 수 기준 배치 묶음 생성
   -> [3] 번역   : pdf_engine.scheduler - 클라우드/로컬 provider에 배치 분배, 재시도/폴백
   -> [4] 재구성 : pdf_engine.rendering - 원문 블록을 redaction으로 제거한 뒤
                   같은 bbox에 번역문 삽입 (세로쓰기 전용 렌더러, OCR 배경 보호 포함)
   -> output.pdf

사용 예:
  export ANTHROPIC_API_KEY=sk-ant-...
  python translate_pdf.py input.pdf -o output_ko.pdf                 # 기본: English -> Korean
  python translate_pdf.py input.pdf --source-lang Japanese --target-lang Korean
  python translate_pdf.py input.pdf --mock                           # API 없이 파이프라인 검증
  python translate_pdf.py input.pdf --dry-run                        # 추출 결과만 출력
  python translate_pdf.py input.pdf --pages 1-3 --export-json t.json # 일부 페이지 + 번역 결과 저장
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError as _pymupdf_error:
    try:
        import fitz as pymupdf
    except ImportError as _fitz_error:
        print("[오류] PyMuPDF 로드 실패.", file=sys.stderr)
        print(f"  pymupdf import 오류: {_pymupdf_error!r}", file=sys.stderr)
        print(f"  fitz import 오류: {_fitz_error!r}", file=sys.stderr)
        print(f"  Python 실행 파일: {sys.executable}", file=sys.stderr)
        print(f"  frozen(EXE): {getattr(sys, 'frozen', False)}", file=sys.stderr)
        sys.exit(1)

# --- pdf_engine 패키지에서 재노출 (하위 호환: 기존 'from translate_pdf import X' 참조 유지) ---
from pdf_engine.config import (
    __version__,
    RUNTIME_REGISTRY, DEFAULT_LOCAL_RUNTIME, get_runtime_spec,
    STOP_EVENT, request_stop, reset_stop, stop_requested,
    SCRIPT_DIR, SYSTEM_PROMPT_PATH, SYSTEM_PROMPT_LOCAL_PATH, USER_TEMPLATE_PATH,
    LETTER_RE, DEFAULT_MODELS, DEFAULT_MODEL, DEFAULT_LOCAL_MODEL_BY_DEVICE,
    model_recipe_device, resolve_local_model_for_device,
    LEMONADE_DEFAULT_PORT, LEMONADE_SERVE_CMD, DEFAULT_TERMINOLOGY_POLICY,
    _parse_local_devices,
)
from pdf_engine.segment import Segment
from pdf_engine.extraction import (
    extract_segments, merge_adjacent_segments,
    resolve_ocr_lang, find_tessdata_dir, OCR_MAX_PLAUSIBLE_FONT_SIZE,
)
from pdf_engine.batching import (
    make_batches, render_segments_block, render_prev_context,
    resolve_source_lang, build_user_prompt,
)
from pdf_engine.providers_cloud import (
    KeyEntry, detect_provider, strip_provider_prefix, load_key_pool,
    resolve_model, build_client, get_key_pool,
    call_claude, call_gemini, call_openai, call_llm, parse_model_json,
    is_rate_limit_error, is_auth_error, is_quota_exhaustion, is_permanent_exhaustion,
    extract_retry_delay,
)
from pdf_engine.providers_local import (
    local_base_url, is_local_runtime_up, lemonade_base_url, is_lemonade_up,
    load_local_model, ensure_local_runtime, ensure_lemonade_server,
    shutdown_local_runtime, shutdown_lemonade_server, make_local_entry,
    detect_quant_bits, local_presets_for, timed_input,
    fetch_local_models, fetch_loaded_local_model, prepare_local_model, choose_local_model,
)
from pdf_engine.scheduler import translate_local_chunked, translate_all_batches, mock_translate
from pdf_engine.rendering import (
    hex_to_rgb01, apply_redactions_safe, insert_translated_text, rebuild_pdf,
)
from pdf_engine.io_utils import (
    load_glossary, parse_pages, export_translations, import_translations,
)
from pdf_engine.filenaming import (
    collapse_to_ranges, parse_resume_filename, sidecar_path_for,
    write_progress_sidecar, load_resume_info, build_output_stem,
)



def parse_args():
    ap = argparse.ArgumentParser(
        description="Anthropic Claude API 기반 레이아웃 보존 PDF 번역기",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input", help="입력 PDF 경로")
    ap.add_argument("-o", "--output", default=None,
                    help="출력 PDF 경로 (기본: <입력파일명>_translated.pdf)")
    ap.add_argument("--source-lang", default="English", help="원문 언어")
    ap.add_argument("--target-lang", default="Korean", help="번역 대상 언어")
    ap.add_argument("--provider", choices=["anthropic", "gemini", "openai"], default=None,
                    help="특정 provider로 제한. 미지정 시 api.txt에 있는 모든 provider의 키를 "
                         "섞어서 순환 사용 (할당량 소진 시 다음 provider/키로 자동 전환)")
    ap.add_argument("--api-key-file", default=None,
                    help="API 키가 담긴 파일 경로 (미지정 시 ./api.txt 자동 탐색). "
                         "한 줄에 키 하나, provider는 키 형태로 자동판별 "
                         "(sk-ant-=anthropic, AIza/AQ.=gemini, sk-=openai). "
                         "'gemini:AIza...'처럼 명시도 가능. 파일 없으면 환경변수 "
                         "ANTHROPIC_API_KEY/GEMINI_API_KEY/OPENAI_API_KEY로 폴백")
    ap.add_argument("--model", default=None,
                    help="--provider를 단일 provider로 지정했을 때만 적용되는 모델 오버라이드. "
                         "여러 provider를 섞어 쓸 때는 --model-anthropic/--model-gemini/"
                         "--model-openai를 사용할 것")
    ap.add_argument("--model-anthropic", default=None,
                    help=f"Anthropic 키에 사용할 모델 (기본: {DEFAULT_MODELS['anthropic']})")
    ap.add_argument("--model-gemini", default=None,
                    help=f"Gemini 키에 사용할 모델 (기본: {DEFAULT_MODELS['gemini']})")
    ap.add_argument("--model-openai", default=None,
                    help=f"OpenAI 키에 사용할 모델 (기본: {DEFAULT_MODELS['openai']}, "
                         f"번역용으로 OpenAI가 권장하는 저비용/고품질 균형 모델)")
    # --- 로컬 NPU/GPU (Lemonade / Ollama / LM Studio 등) 폴백 ---
    ap.add_argument("--local-runtime", default=DEFAULT_LOCAL_RUNTIME,
                    choices=list(RUNTIME_REGISTRY),
                    help=f"클라우드 API 소진 시 폴백으로 쓸 로컬 AI 런타임 (기본: {DEFAULT_LOCAL_RUNTIME}). "
                         f"지원: {', '.join(RUNTIME_REGISTRY)}. NPU 가속은 지금 lemonade(AMD XDNA2)만 "
                         f"지원하고 나머지는 GPU만 지원한다.")
    ap.add_argument("--local-device", default=None,
                    help="로컬 폴백에 사용할 장치, 콤마로 여러 개 지정 가능 (예: 'npu', 'gpu', 'npu,gpu'). "
                         "먼저 지정한 장치를 우선 사용하고, 그 장치 쪽 키가 전부 죽어야 다음 장치로 "
                         "넘어간다. --local-npu(하위호환)만 주면 'npu'와 동일하게 동작한다.")
    ap.add_argument("--local-npu", action="store_true",
                    help="(하위호환 별칭) --local-device npu 와 동일. 클라우드 API가 전부 소진되면 "
                         "로컬 NPU로 이어서 번역. 서버가 안 떠 있으면 자동으로 백그라운드 기동함")
    ap.add_argument("--no-local-npu", action="store_true",
                    help="로컬 폴백을 비활성화 (--local-device/--local-npu가 지정돼 있어도 무시)")
    ap.add_argument("--model-local", default=None,
                    help=f"로컬 모델 이름 (기본: device별로 자동 선택, "
                         f"npu={DEFAULT_LOCAL_MODEL_BY_DEVICE['npu']}, "
                         f"gpu={DEFAULT_LOCAL_MODEL_BY_DEVICE['gpu']}). "
                         f"NPU/GPU를 동시에 쓸 땐 --model-local-npu/--model-local-gpu로 "
                         f"각각 따로 지정하는 걸 권장 (모델은 장치별 recipe가 고정돼 있어서 "
                         f"하나의 모델로 NPU/GPU를 겸용할 수 없다).")
    ap.add_argument("--model-local-npu", default=None,
                    help=f"NPU 전용 모델 지정 (기본: {DEFAULT_LOCAL_MODEL_BY_DEVICE['npu']})")
    ap.add_argument("--model-local-gpu", default=None,
                    help=f"GPU 전용 모델 지정 (기본: {DEFAULT_LOCAL_MODEL_BY_DEVICE['gpu']})")
    ap.add_argument("--local-port", type=int, default=None,
                    help="로컬 런타임 포트 (기본: 선택한 --local-runtime의 기본 포트, "
                         f"lemonade={RUNTIME_REGISTRY['lemonade']['default_port']}, "
                         f"ollama={RUNTIME_REGISTRY['ollama']['default_port']}, "
                         f"lmstudio={RUNTIME_REGISTRY['lmstudio']['default_port']})")
    ap.add_argument("--local-serve-cmd", default=None,
                    help="로컬 런타임 실행 명령/경로 직접 지정 (기본: RUNTIME_REGISTRY의 후보를 자동 탐색)")
    ap.add_argument("--local-timeout", type=float, default=300.0,
                    help="로컬 NPU/GPU 요청 타임아웃(초). 느릴 수 있으므로 넉넉하게 (기본: 300)")
    ap.add_argument("--local-ctx-size", type=int, default=8192,
                    help="(Lemonade 전용) 로컬 모델 로드시 컨텍스트 크기(토큰). 번역 프롬프트가 길어 "
                         "기본값(보통 4096 이하)으로는 JSON 출력 지시가 잘릴 수 있어 넉넉히 잡음 (기본: 8192)")
    ap.add_argument("--model-select-timeout", type=float, default=10.0,
                    help="(Lemonade 전용) 로컬 모델 선택 메뉴의 입력 대기 시간(초). 시간 내 입력이 없으면 "
                         "기본 모델로 자동 진행 (기본: 10)")
    ap.add_argument("--no-merge", action="store_true",
                    help="문장 단절 블록 자동 병합 비활성화 (PDF가 문장을 여러 블록으로 쪼갠 경우 "
                         "기본적으로 병합해서 번역 품질을 높임)")
    ap.add_argument("--no-compress", action="store_true",
                    help="번역 후 PDF 압축본(<파일명>_compressed.pdf) 생성을 건너뜀. "
                         "기본적으로는 저장 직후 자동으로 압축본을 별도 생성한다(원본은 그대로 유지).")
    ap.add_argument("--tessdata-dir", default=None,
                    help="Tesseract 언어 데이터(tessdata) 폴더 직접 지정. 자동 탐지가 실패할 때 사용 "
                         "(Windows 기본 경로 예: 'C:\\Program Files\\Tesseract-OCR\\tessdata')")
    ap.add_argument("--ocr-lang", default=None,
                    help="OCR 언어 코드 (Tesseract 언어 데이터 이름 기준, 예: eng/jpn/kor/jpn_vert). "
                         "여러 언어를 함께 인식하려면 '+'로 연결: 'eng+jpn'. "
                         "기본값: --source-lang에서 자동 매핑(예: Japanese -> jpn+jpn_vert)")
    ap.add_argument("--doc-type", default="technical documentation")
    ap.add_argument("--style", default="formal, professional")
    ap.add_argument("--terminology-policy", default=DEFAULT_TERMINOLOGY_POLICY)
    ap.add_argument("--title", default=None, help="문서 제목 (기본: PDF 메타데이터/파일명)")
    ap.add_argument("--domain", default="general")
    ap.add_argument("--instructions", default="", help="추가 문서별 지시사항")
    ap.add_argument("--glossary", default=None, help="용어집 파일 (.json 또는 .csv/.txt)")
    ap.add_argument("--pages", default=None, help='번역할 페이지 지정 (예: "1-3,7")')
    ap.add_argument("--batch-chars", type=int, default=1500, help="배치당 최대 원문 문자 수")
    ap.add_argument("--batch-segs", type=int, default=10, help="배치당 최대 세그먼트 수")
    ap.add_argument("--max-tokens", type=int, default=8192, help="응답 max_tokens")
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="샘플링 온도. 미지원 모델이면 자동 제거 후 재시도")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="배치당 최대 재시도 횟수 (429/할당량 초과는 별도 무제한 처리, 이 값과 무관)")
    ap.add_argument("--min-interval", type=float, default=0.0,
                    help="API 요청 사이 최소 간격(초). 분당 요청 제한(RPM)에 걸리면 "
                         "예: 무료/저티어는 6~10 정도로 설정해 선제적으로 속도 조절")
    ap.add_argument("--max-rate-limit-retries", type=int, default=0,
                    help="429(할당량 초과) 재시도 최대 횟수. 0=무제한 (서버가 알려준 대기시간만큼 "
                         "자동으로 자고 계속 재시도)")
    ap.add_argument("--font-scale", type=float, default=1.0,
                    help="삽입 폰트 크기 배율 (번역문이 자주 잘리면 0.9 등으로 축소)")
    ap.add_argument("--translate-all", action="store_true",
                    help="숫자/기호 전용 블록도 API로 전송 (기본은 원문 유지)")
    ap.add_argument("--mock", action="store_true",
                    help="API 호출 없이 '[모의 번역] 원문' 삽입 — 레이아웃/폰트 검증용")
    ap.add_argument("--dry-run", action="store_true", help="추출 결과만 출력하고 종료")
    ap.add_argument("--export-json", default=None, help="번역 결과를 JSON으로 저장")
    ap.add_argument("--import-json", default=None,
                    help="저장된 JSON으로 재구성만 수행 (API 호출 생략)")
    return ap.parse_args()


# _parse_local_devices는 pdf_engine.config에서 import됨 (위 참고)


def main():
    args = parse_args()

    # 로컬 런타임/포트 정규화: --local-port를 안 줬으면 선택한 런타임의 기본 포트를 쓴다.
    if args.local_port is None:
        args.local_port = get_runtime_spec(args.local_runtime)["default_port"]

    in_path = Path(args.input)
    if not in_path.exists():
        sys.exit(f"[오류] 입력 파일이 없습니다: {in_path}")

    # 이어서 번역: 파일명에 이전 실행의 페이지 범위 정보가 있으면 감지
    resume_info = load_resume_info(in_path)
    if resume_info:
        u_ranges = resume_info["u_ranges"]
        if u_ranges:
            ranges_str = ", ".join(f"{a}-{b}" for a, b in u_ranges)
            print(f"[정보] 이어서-번역 파일명 감지: 전체 스코프 "
                  f"{resume_info['t_start']}-{resume_info['t_end']}페이지, "
                  f"미번역 {ranges_str}페이지")
        else:
            print(f"[정보] 이어서-번역 파일명 감지: "
                  f"{resume_info['t_start']}-{resume_info['t_end']}페이지 전체 완역 상태")
        if not u_ranges:
            if args.pages is None:
                print("[안내] 미번역 구간이 없어(완역 상태) 다시 번역할 것이 없습니다. 종료합니다.")
                return
        elif args.pages is None:
            args.pages = ",".join(f"{a}-{b}" for a, b in u_ranges)
            print(f"[정보] --pages 미지정 -> 미번역 구간({args.pages})만 이어서 번역")
    base_stem = resume_info["base"] if resume_info else in_path.stem

    doc = pymupdf.open(in_path)
    if doc.needs_pass:
        sys.exit("[오류] 암호화된 PDF입니다. 먼저 암호를 해제하세요 (예: qpdf --decrypt).")
    if args.title is None:
        args.title = (doc.metadata or {}).get("title") or in_path.stem

    # [1] 추출
    page_filter = parse_pages(args.pages, doc.page_count)
    segments = extract_segments(doc, page_filter, args.translate_all,
                                tessdata_dir=args.tessdata_dir,
                                ocr_lang=resolve_ocr_lang(args.source_lang, args.ocr_lang))
    if not args.no_merge:
        segments = merge_adjacent_segments(segments)
    n_target = sum(1 for s in segments if s.needs_translation)
    n_skip = len(segments) - n_target
    total_chars = sum(len(s.text) for s in segments if s.needs_translation)
    print(f"[1/4] 추출: {doc.page_count}페이지, 텍스트 블록 {len(segments)}개 "
          f"(번역 대상 {n_target}, 원문 유지 {n_skip}), 원문 약 {total_chars:,}자")
    if n_target == 0:
        print("[안내] 번역할 텍스트를 찾지 못했습니다. 스캔(이미지) PDF인데 OCR 결과가 "
              "전부 신뢰도 필터에 걸렀거나(위 로그에 [OCR 필터]/[경고] 확인), 이미 완역된 "
              "파일일 수 있습니다. 번역 단계를 건너뛰고 원본 내용 그대로 저장합니다.")

    if args.dry_run:
        for s in segments:
            flag = "T" if s.needs_translation else "-"
            preview = s.text.replace("\n", "⏎")[:70]
            print(f"  [{flag}] {s.seg_id}  {s.font_size:>5.1f}pt  {preview}")
        return

    if page_filter is None:
        run_first_page, run_last_page = 1, doc.page_count
    else:
        run_first_page, run_last_page = min(page_filter) + 1, max(page_filter) + 1

    # [2]+[3] 번역
    aborted = False
    if n_target == 0:
        pass  # 위에서 이미 안내함 - API 키 확인/배치 구성 등 번역 단계 전체를 건너뜀
    elif args.import_json:
        import_translations(args.import_json, segments)
    elif args.mock:
        print("[2/4] 배치: (mock 모드 — API 호출 생략)")
        mock_translate(segments)
        print("[3/4] 모의 번역 완료")
    else:
        batches = list(make_batches([s for s in segments if s.needs_translation],
                                    args.batch_chars, args.batch_segs))
        pool = get_key_pool(args)
        # 로컬 NPU/GPU 폴백: --local-device로 지정한 장치들을 풀 맨 뒤(최후 순위)에 추가.
        # 여러 장치를 지정하면 먼저 적은 장치부터 순서대로 시도한다(예: "npu,gpu" -> NPU 우선,
        # NPU 쪽 키가 전부 죽어야 GPU로 넘어감 - next_alive_index가 pool 등장 순서를 따름).
        requested_devices = _parse_local_devices(args)
        if requested_devices:
            runtime = args.local_runtime
            spec = get_runtime_spec(runtime)
            added = []
            for device in requested_devices:
                supports_key = f"supports_{device}"
                if not spec.get(supports_key, False):
                    print(f"[정보] {spec['label']}는 {device.upper()}를 지원하지 않아 건너뜀 "
                          f"(--local-runtime {runtime} --local-device {device})")
                    continue
                entry = make_local_entry(args, device=device)
                pool.append(entry)
                added.append(entry.label)
            if added:
                print(f"[정보] 로컬 폴백 활성화: {', '.join(added)} "
                      f"(모델 {args.model_local or DEFAULT_MODELS['local']}, "
                      f"포트 {args.local_port}, 클라우드 전부 소진 시에만 사용)")
        print(f"[2/4] 배치: {len(batches)}개 (배치당 최대 {args.batch_chars}자 / "
              f"{args.batch_segs}세그먼트)")
        system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        system_prompt_local = SYSTEM_PROMPT_LOCAL_PATH.read_text(encoding="utf-8") \
            if SYSTEM_PROMPT_LOCAL_PATH.exists() else None
        template = USER_TEMPLATE_PATH.read_text(encoding="utf-8")
        glossary_text = load_glossary(args.glossary)
        try:
            aborted = translate_all_batches(pool, args, system_prompt, template,
                                            segments, glossary_text,
                                            system_prompt_local=system_prompt_local)
        finally:
            shutdown_local_runtime()

    if args.export_json:
        export_translations(args.export_json, segments)

    # 이번 실행에서 (사유 불문하고) 결국 원문으로 남은 페이지를 전부 집계
    # -> 산발적으로 흩어진 실패도 각각의 구간으로 표현된다.
    target_segments = [s for s in segments if s.needs_translation]
    run_failed_pages = sorted({s.page + 1 for s in target_segments if s.translation_failed})
    run_untranslated_ranges = collapse_to_ranges(run_failed_pages)

    # '_translated_###-@@@'는 이 문서가 다루는 전체 페이지 범위(스코프)를 뜻하며,
    # 이어서-번역 시에는 최초 실행의 스코프를 그대로 유지한다(재번역 대상 페이지만 좁혀서 처리해도 안 바뀜).
    # 파일명 상태는 "실제로 번역 완료된 페이지 집합"을 누적 관리한다.
    # 이전 번역 완료 페이지 + 이번 실행에서 성공한 페이지를 합치고,
    # 전체 PDF 페이지 집합에서 이를 뺀 나머지를 미번역 페이지로 기록한다.
    # '_translated_###-@@@'는 이 문서가 다루는 전체 페이지 범위(스코프)를 뜻한다.
    # 스코프는 "문서 전체"가 아니라 "이번 작업이 다루기로 한 범위"다:
    #   - 최초 실행: --pages로 좁혔으면 그 범위, 안 좁혔으면 문서 전체
    #   - 이어서 번역: 최초 실행 때의 스코프를 그대로 유지 (재번역 대상만 좁혀도 안 바뀜)
    # 문서 전체로 고정하면, 처음부터 특정 챕터만 번역할 의도로 --pages를 준 경우에도
    # 나머지 미지정 페이지 전부가 "미번역"으로 잘못 표시된다 (예: 77-78만 지정했는데
    # 1-1095 전체가 미번역으로 찍히는 버그) - 그래서 반드시 스코프 안에서만 계산한다.
    if resume_info:
        scope_pages = set(range(resume_info["t_start"], resume_info["t_end"] + 1))
    else:
        scope_pages = set(range(run_first_page, run_last_page + 1))

    if resume_info:
        prior_scope = set(range(resume_info["t_start"], resume_info["t_end"] + 1))
        prior_untranslated = set()
        for a, b in resume_info["u_ranges"]:
            prior_untranslated.update(range(a, b + 1))
        prior_translated = prior_scope - prior_untranslated
    else:
        prior_translated = set()

    current_target_pages = {s.page + 1 for s in target_segments}
    current_failed_pages = set(run_failed_pages)
    current_success_pages = current_target_pages - current_failed_pages
    final_translated_pages = prior_translated | current_success_pages
    final_untranslated_pages = scope_pages - final_translated_pages

    final_translated_ranges = collapse_to_ranges(sorted(final_translated_pages))
    final_untranslated_ranges = collapse_to_ranges(sorted(final_untranslated_pages))

    # [4] 재구성
    # 중요: 대형/구조가 복잡한 PDF 전체를 doc.save(..., garbage=3)로 다시 쓰면
    # 원본의 손상된/비표준 xref 객체까지 전부 재해석하면서 MuPDF repair 오류가 날 수 있다.
    # 따라서 원본 파일을 먼저 임시 출력으로 그대로 복사한 뒤, 그 복사본에서
    # 실제 번역 대상 페이지만 수정하고 증분 저장(saveIncr)한다.
    # 이렇게 하면 지정 범위 밖 페이지는 재생성/변환하지 않고 원본 바이트 구조를 유지한다.
    if args.output:
        out_path = Path(args.output)
        needs_sidecar = True  # -o로 직접 지정해도 정확한 진행정보는 항상 남겨둔다
    else:
        out_stem, needs_sidecar = build_output_stem(base_stem, final_translated_ranges,
                                                    final_untranslated_ranges)
        out_path = in_path.with_name(out_stem + in_path.suffix)

    if needs_sidecar:
        write_progress_sidecar(out_path, base_stem, final_translated_ranges,
                               final_untranslated_ranges, doc.page_count)
        print(f"[정보] 미번역 구간이 많아 파일명을 압축 표기(MULTIn)했습니다. "
              f"정확한 페이지 목록은 {sidecar_path_for(out_path).name}에 저장됨 - "
              f"이어서 번역할 때 이 파일도 PDF와 같은 폴더에 있어야 합니다.")

    tmp_path = out_path.with_name(out_path.stem + ".tmp" + out_path.suffix)
    if tmp_path.exists():
        tmp_path.unlink()

    # 추출에 사용한 문서는 더 이상 저장하지 않는다.
    doc_page_count = doc.page_count  # close() 후에도 검증용으로 참조해야 하므로 미리 저장
    doc.close()
    shutil.copy2(in_path, tmp_path)

    work_doc = None
    try:
        work_doc = pymupdf.open(tmp_path)
        if work_doc.needs_pass:
            raise RuntimeError("임시 출력 PDF가 암호화되어 있습니다")

        truncated = rebuild_pdf(work_doc, segments, args.font_scale)
        try:
            # 전체 문서 폰트 재구성은 하지 않는다. subset_fonts()가 비대상 페이지의
            # 객체까지 순회할 수 있으므로 부분 번역 모드에서는 오히려 위험하다.
            pass
        except Exception:
            pass

        # 원본 복사본에 변경 객체만 추가 기록. 지정 범위 밖 페이지는 그대로 유지.
        if work_doc.can_save_incrementally():
            work_doc.saveIncr()
            work_doc.close()
            work_doc = None
        else:
            # MuPDF가 원본을 repair하여 증분 저장이 불가능한 경우의 안전 폴백.
            # PDF 페이지를 이미지/텍스트로 재렌더링하지 않는다. 수정 페이지만 별도 PDF로
            # 내보낸 뒤 pypdf가 원본 페이지 객체를 복제하고 해당 페이지만 교체한다.
            # 따라서 비대상 페이지의 글꼴/좌표/콘텐츠 스트림은 재조판되지 않는다.
            patch_path = tmp_path.with_name(tmp_path.stem + ".patch.pdf")
            patched_pages = sorted(current_target_pages)
            patch_doc = pymupdf.open()
            for page_no in patched_pages:
                patch_doc.insert_pdf(work_doc, from_page=page_no - 1, to_page=page_no - 1)
            patch_doc.save(patch_path, garbage=0, deflate=False)
            patch_doc.close()
            work_doc.close()
            work_doc = None

            try:
                from pypdf import PdfReader, PdfWriter
            except ImportError as ie:
                raise RuntimeError(
                    "증분 저장 불가 PDF 폴백에 pypdf가 필요합니다. "
                    "먼저 'pip install pypdf'를 실행하세요."
                ) from ie

            src_reader = PdfReader(str(in_path), strict=False)
            patch_reader = PdfReader(str(patch_path), strict=False)
            writer = PdfWriter()
            patch_map = {pno: i for i, pno in enumerate(patched_pages)}
            for pno in range(1, len(src_reader.pages) + 1):
                if pno in patch_map:
                    writer.add_page(patch_reader.pages[patch_map[pno]])
                else:
                    writer.add_page(src_reader.pages[pno - 1])
            with open(tmp_path, "wb") as fp:
                writer.write(fp)
            try:
                patch_path.unlink()
            except Exception:
                pass
            print(f"[저장] 증분 저장 불가 -> pypdf 페이지 객체 교체 폴백 사용 ({len(patched_pages)}페이지 수정)")

        # 저장 후 새 프로세스 관점으로 다시 열어 페이지 수와 접근 가능 여부 검증
        check = pymupdf.open(tmp_path)
        expected_pages = doc_page_count
        if check.page_count != expected_pages:
            raise RuntimeError(
                f"저장 결과 페이지 수 불일치: 기대 {expected_pages}, 실제 {check.page_count}"
            )
        # 모든 페이지를 강제로 파싱하면 원본의 비표준 객체 때문에 불필요한 repair가
        # 발생할 수 있다. 수정한 페이지만 실제 접근 검증한다.
        verify_pages = sorted(current_target_pages)
        for page_no in verify_pages:
            _ = check[page_no - 1].rect
            _ = check[page_no - 1].get_text("text")
        check.close()

    except Exception as e:
        if work_doc is not None:
            try:
                work_doc.close()
            except Exception:
                pass
        try:
            tmp_path.unlink()
        except Exception:
            pass
        sys.exit(f"[오류] 출력 PDF 저장/검증 실패. 기존 출력 파일은 교체하지 않았습니다: {e}")

    os.replace(tmp_path, out_path)
    status = "번역 일부 미완료(원문 유지)" if aborted else "완료"
    print(f"[4/4] 재구성 {status} -> {out_path}"
          + (f" (축소 한계 초과 {truncated}개 블록)" if truncated else ""))
    if final_untranslated_ranges:
        ranges_str = ", ".join(f"{a}-{b}" for a, b in final_untranslated_ranges)
        print(f"[안내] {ranges_str}페이지는 원문 그대로 저장됨 "
              f"({sum(1 for s in target_segments if s.translation_failed)}개 세그먼트). "
              f"이 출력 파일을 다시 입력으로 넣으면 파일명에서 미번역 구간을 읽어 자동으로 이어서 번역함:")
        print(f"       python {Path(__file__).name} \"{out_path}\""
              + (f" --provider {args.provider}" if args.provider else ""))
    else:
        print("[안내] 미번역 구간 없음 (완역). 이 파일을 다시 입력으로 넣으면 할 일이 없어 그대로 종료됨.")

    final_path = out_path
    if not getattr(args, "no_compress", False):
        compressed = compress_pdf(out_path)
        if compressed is not None:
            final_path = compressed
    # GUI(및 다른 자동화 도구)가 "번역이 끝나고 최종적으로 열어야 할 파일"을 명확히
    # 파싱할 수 있도록 고유 마커 라인으로 출력한다 (압축본이 있으면 압축본, 없으면 원본).
    print(f"[최종파일] {final_path}")

def compress_pdf(src_path: Path) -> Path | None:
    """
    최종 산출물을 별도 파일(<stem>_compressed.pdf)로 재저장하며 최적화한다.
    saveIncr/pypdf 폴백 저장은 안정성을 위해 원본 바이트 구조를 최대한 보존하는 대신
    가비지 컬렉션/폰트 서브셋 같은 최적화를 하지 않아 용량이 커진다(특히 CJK 폰트를
    새로 임베드하는 번역 PDF에서 두드러짐). 저장이 끝난 뒤 완성본을 다시 열어
    한 번 더 최적화 저장하는 후처리 단계로 분리해, 기존의 검증된 안전한 저장 경로는
    건드리지 않는다. 원본(비압축) 파일은 그대로 남기고 압축본만 별도로 만든다.
    실패해도 원본엔 영향 없음(예외를 잡아 None 반환, 원본 그대로 사용).
    """
    compressed_path = src_path.with_name(src_path.stem + "_compressed" + src_path.suffix)
    try:
        cdoc = pymupdf.open(src_path)
        try:
            cdoc.subset_fonts()  # 완성된 최종본이라 전체 서브셋해도 안전 (증분저장 중엔 위험했음)
        except Exception:
            pass
        cdoc.save(compressed_path, garbage=4, deflate=True, deflate_images=True,
                  deflate_fonts=True, clean=True)
        cdoc.close()
        before = src_path.stat().st_size
        after = compressed_path.stat().st_size
        pct = 100.0 * (1 - after / before) if before else 0.0
        print(f"[압축] {before / 1048576:.1f}MB -> {after / 1048576:.1f}MB "
              f"({pct:.0f}% 감소) -> {compressed_path.name}")
        return compressed_path
    except Exception as e:
        print(f"[압축][경고] 압축 저장 실패(원본 파일은 그대로 유지됨): {e}")
        try:
            if compressed_path.exists():
                compressed_path.unlink()
        except Exception:
            pass
        return None


if __name__ == "__main__":
    main()