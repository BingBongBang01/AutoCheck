"""
번역 오케스트레이션: 세그먼트를 배치로 나눠 API 키 풀/로컬 런타임에 분배하고,
실패/할당량 소진 시 다음 키로 전환하거나 로컬 폴백으로 넘어가는 전체 흐름을 담당한다.
"""
from __future__ import annotations

import json
import re
import time

from .config import STOP_EVENT, model_recipe_device, resolve_local_model_for_device
from .segment import Segment
from .batching import make_batches, render_prev_context, build_user_prompt
from .providers_cloud import (
    KeyEntry, call_llm, parse_model_json,
    is_rate_limit_error, is_auth_error, is_quota_exhaustion, is_permanent_exhaustion,
    extract_retry_delay, _is_context_or_400_error,
)
from .providers_local import ensure_local_runtime, local_presets_for

def translate_local_chunked(entry: "KeyEntry", args, system_prompt_local: str,
                            template: str, glossary_text: str, prev_context: str,
                            todo: list[Segment], presets: dict) -> tuple[dict[str, str], set[str]]:
    """
    로컬 NPU용 청크 처리: todo를 양자화 프리셋(batch_chars/batch_segs)에 맞춰
    작은 청크로 나눠 순차 호출하고, 400/컨텍스트 오류가 나면 해당 청크를
    반으로 쪼개 재귀적으로 재시도한다 (1개까지 쪼개도 실패하면 그 세그먼트만 포기).
    반환: (합쳐진 seg_id->번역문 매핑, 포기한 seg_id 집합).
    포기 집합을 돌려주지 않으면 호출측 while 루프가 같은 세그먼트를 무한 재요청하게 된다.
    """
    chunks: list[list[Segment]] = []
    cur, cur_chars = [], 0
    for s in todo:
        if cur and (cur_chars + len(s.text) > presets["batch_chars"]
                    or len(cur) >= presets["batch_segs"]):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(s)
        cur_chars += len(s.text)
    if cur:
        chunks.append(cur)

    result: dict[str, str] = {}
    given_up: set[str] = set()

    def run_chunk(chunk: list[Segment], depth: int = 0):
        prompt = build_user_prompt(template, args, glossary_text, prev_context, chunk)
        try:
            raw = call_llm(entry.provider, entry.client, entry.model,
                          system_prompt_local, prompt,
                          presets["max_tokens"], args.temperature)
            result.update(parse_model_json(raw))
        except Exception as e:
            if _is_context_or_400_error(e) and len(chunk) > 1 and depth < 4:
                mid = len(chunk) // 2
                print(f"    [로컬] 청크 오류({str(e)[:80]}) -> {len(chunk)}세그먼트를 "
                      f"반으로 쪼개 재시도")
                run_chunk(chunk[:mid], depth + 1)
                run_chunk(chunk[mid:], depth + 1)
            elif len(chunk) == 1:
                print(f"    [로컬] 세그먼트 {chunk[0].seg_id} 번역 실패(원문 유지 예정): "
                      f"{str(e)[:120]}")
                given_up.add(chunk[0].seg_id)
            else:
                raise  # 컨텍스트류가 아닌 오류는 상위 재시도 로직에 위임

    total = len(chunks)
    for ci, chunk in enumerate(chunks, 1):
        if STOP_EVENT.is_set():
            print(f"    [로컬] 사용자 중단 요청 -> 남은 {total - ci + 1}개 청크 건너뜀")
            break
        if total > 1:
            print(f"    [로컬] 청크 {ci}/{total} ({len(chunk)}세그먼트) 처리 중...")
        run_chunk(chunk)
    return result, given_up


def translate_all_batches(pool: list["KeyEntry"], args, system_prompt: str, template: str,
                          segments: list[Segment], glossary_text: str,
                          system_prompt_local: str | None = None) -> bool:
    """
    반환값: aborted (pool의 모든 키가 영구 소진되어 나머지 배치를 건너뛰고 중단했는지 여부).
    어떤 이유로든(할당량 소진, 일반 오류, JSON 파싱 실패 등) 결국 원문으로 남은 세그먼트는
    s.translation_failed=True로 표시되며, 이는 main()에서 페이지 단위로 집계해
    출력 파일명의 미번역 구간을 만드는 데 쓰인다.
    로컬 NPU 엔트리는 컨텍스트가 짧고 소형 모델이라 system_prompt_local(축약판, 없으면
    system_prompt와 동일)을 사용한다.
    """
    system_prompt_local = system_prompt_local or system_prompt
    targets = [s for s in segments if s.needs_translation]
    batches = list(make_batches(targets, args.batch_chars, args.batch_segs))
    prev_pairs: list[tuple[str, str]] = []
    total_chars_sent = 0
    last_call_time = 0.0
    n_keys = len(pool)
    key_idx = 0  # 배치 간에도 유지
    local_started = {"done": False, "presets": None}  # 로컬 서버를 아직 안 깨웠으면 done=False

    def next_alive_index(start: int) -> int | None:
        """
        살아있는 키 중 다음 것을 고른다. 단 클라우드 키를 항상 로컬보다 우선한다
        (로컬 NPU는 클라우드가 전부 죽은 최후의 폴백).
        할당량 소진으로 죽었던 키는 revive_at(서버가 알려준 리셋 시각)이 지나면
        자동으로 부활시킨다 -> 로컬 NPU로 작업 중이어도 다음 배치부터 API로 복귀.
        """
        now = time.monotonic()
        for e2 in pool:
            if not e2.alive and e2.revive_at is not None and now >= e2.revive_at:
                e2.alive = True
                e2.revive_at = None
                print(f"  [부활] 키 {e2.label} 할당량 리셋 시간 경과 -> 재시도 대상으로 복귀")
        cloud = [i for i in range(n_keys) if pool[i].alive and not pool[i].is_local]
        local = [i for i in range(n_keys) if pool[i].alive and pool[i].is_local]
        if cloud:
            # start 이상에서 첫 클라우드 키, 없으면 처음부터 (라운드로빈)
            for i in range(start, start + n_keys):
                j = i % n_keys
                if j in cloud:
                    return j
            return cloud[0]
        # 클라우드가 전부 죽음 -> 로컬 사용 (필요 시 서버 기동)
        if local:
            if not local_started["done"]:
                had_cloud = any(not e.is_local for e in pool)
                reason = "클라우드 API 전부 소진" if had_cloud else "클라우드 API 키 없음"
                local_label = pool[local[0]].label
                print(f"  [폴백] {reason} -> {local_label}(으)로 전환 시도")
                if not ensure_local_runtime(args):
                    # 서버 기동 실패 -> 로컬도 못 쓰므로 죽은 것으로 처리
                    for i in local:
                        pool[i].alive = False
                    return _wait_for_revival()
                # 메뉴에서 선택된(또는 기본) 모델을 반영하되, 각 로컬 엔트리는 자기 device
                # (NPU/GPU)에 맞는 모델만 받는다 - 예전엔 전부 같은 모델로 덮어써서 GPU
                # 엔트리도 NPU 전용(-FLM) 모델을 강제로 받는 버그가 있었다(Lemonade는 장치를
                # 모델의 recipe로 고정하므로, 이러면 "GPU 체크"가 무시되고 NPU만 도는 결과가 됨).
                for i in local:
                    device_of_entry = model_recipe_device(pool[i].model)
                    pool[i].model = resolve_local_model_for_device(args, device_of_entry)
                local_started["presets"] = local_presets_for(pool[local[0]].model)
                local_started["done"] = True
            return local[0]
        return _wait_for_revival()

    def _wait_for_revival() -> int | None:
        """
        번역 수단이 하나도 없지만 할당량 리셋으로 부활 예정인 키가 있으면
        (10분 이내 한정) 그때까지 대기했다가 부활시켜 반환. 없으면 None(중단).
        """
        pending = [e2 for e2 in pool if not e2.alive and e2.revive_at is not None]
        if not pending:
            return None
        soonest = min(e2.revive_at for e2 in pending)
        wait = soonest - time.monotonic()
        if wait > 600:
            print(f"  [대기 포기] 가장 빠른 할당량 리셋까지 {wait/60:.0f}분 남음 (10분 초과) -> 중단")
            return None
        if wait > 0:
            print(f"  [대기] 번역 수단 없음. 할당량 리셋까지 {wait:.0f}초 대기...")
            end = time.monotonic() + wait
            while time.monotonic() < end:
                if STOP_EVENT.is_set():
                    return None
                time.sleep(min(1.0, end - time.monotonic()))
        return next_alive_index(0)

    aborted = False
    abort_page: int | None = None
    stopped_by_user = False
    total_pages = len({s.page for s in targets})  # 번역 대상이 있는 페이지 수 (진행 표시용)
    pages_done: set[int] = set()

    for bi, batch in enumerate(batches, 1):
        if STOP_EVENT.is_set():
            stopped_by_user = True
            first_left = min(s.page for s in batch) + 1
            print(f"  [중단] 사용자 요청 -> {bi}번째 배치({first_left}페이지)부터 원문 유지하고 저장 진행")
            break
        remaining = {s.seg_id: s for s in batch}
        attempt = 0
        rl_retry = 0
        keys_tried_since_success = 0
        no_progress = 0  # 성공 응답인데 remaining이 줄지 않은 연속 횟수 (모델의 세그먼트 누락 반복 감지)
        while remaining:
            if STOP_EVENT.is_set():
                stopped_by_user = True
                print(f"  [중단] 사용자 요청 -> 현재 배치의 남은 {len(remaining)}개 세그먼트는 "
                      f"원문 유지하고 저장 진행")
                break
            idx = next_alive_index(key_idx)
            if idx is None:
                aborted = True
                abort_page = min(s.page for s in remaining.values()) + 1
                print(f"  [batch {bi}/{len(batches)}] 사용 가능한 번역 수단이 없음 "
                      f"-> {abort_page}페이지부터 번역 중단, 원문 유지")
                break
            key_idx = idx
            entry = pool[key_idx]

            todo = list(remaining.values())
            prompt = build_user_prompt(template, args, glossary_text,
                                       render_prev_context(prev_pairs), todo)

            if args.min_interval > 0:
                wait = args.min_interval - (time.monotonic() - last_call_time)
                if wait > 0:
                    time.sleep(wait)

            try:
                if entry.is_local:
                    # 로컬 NPU: 양자화 프리셋에 맞춰 작은 청크로 나눠 처리
                    # (소형 모델 컨텍스트/품질 한계 -> 실패 파장 축소)
                    presets = local_started["presets"] or local_presets_for(entry.model)
                    mapping, given_up = translate_local_chunked(
                        entry, args, system_prompt_local, template, glossary_text,
                        render_prev_context(prev_pairs), todo, presets)
                    # 반분 재시도 끝에도 실패한 세그먼트는 원문 유지로 확정하고
                    # remaining에서 제거 (안 그러면 무한 재요청 루프)
                    for sid in given_up:
                        if sid in remaining:
                            remaining[sid].translated = remaining[sid].text
                            remaining[sid].translation_failed = True
                            del remaining[sid]
                else:
                    raw = call_llm(entry.provider, entry.client, entry.model,
                                  system_prompt, prompt,
                                  args.max_tokens, args.temperature)
                    mapping = parse_model_json(raw)
                last_call_time = time.monotonic()
                keys_tried_since_success = 0
            except Exception as e:
                last_call_time = time.monotonic()
                if is_permanent_exhaustion(e):
                    # 인증/차단(진짜 영구) vs 할당량 소진(리셋 시간 지나면 부활 가능) 구분
                    entry.alive = False
                    if is_auth_error(e):
                        entry.revive_at = None
                        print(f"  [batch {bi}/{len(batches)}] 키 {entry.label} 영구 오류(인증/권한/결제) "
                              f"-> 이번 실행에서 완전 제외: {str(e)[:200]}")
                    else:
                        # 할당량 소진: 서버 제시 리셋 시간(없으면 30분) 후 자동 부활 예약
                        delay = extract_retry_delay(e, default=1800.0)
                        entry.revive_at = time.monotonic() + delay
                        print(f"  [batch {bi}/{len(batches)}] 키 {entry.label} 할당량 소진 "
                              f"-> {delay/60:.0f}분 후 자동 재시도 예약 (그동안 다른 키/NPU 사용)")
                    nxt = next_alive_index(key_idx + 1)
                    if nxt is None:
                        aborted = True
                        abort_page = min(s.page for s in remaining.values()) + 1
                        print(f"  [batch {bi}/{len(batches)}] 모든 키 소진 "
                              f"-> {abort_page}페이지부터 번역 중단, 원문 유지")
                        break
                    key_idx = nxt
                    continue
                if is_rate_limit_error(e):
                    # 일시적 429(분당 제한 등): 다음 살아있는 키로 즉시 전환
                    rl_retry += 1
                    if args.max_rate_limit_retries and rl_retry > args.max_rate_limit_retries:
                        print(f"  [batch {bi}/{len(batches)}] 일시적 할당량 재시도 한도 "
                              f"({args.max_rate_limit_retries}회) 초과 -> 이 배치 포기")
                        break
                    prev_label = entry.label
                    nxt = next_alive_index(key_idx + 1)
                    if nxt is None:
                        aborted = True
                        abort_page = min(s.page for s in remaining.values()) + 1
                        break
                    key_idx = nxt
                    keys_tried_since_success += 1
                    n_alive = sum(1 for e2 in pool if e2.alive)
                    if keys_tried_since_success % max(n_alive, 1) == 0:
                        delay = extract_retry_delay(e)
                        print(f"  [batch {bi}/{len(batches)}] 살아있는 키 {n_alive}개 전부 "
                              f"일시적 할당량 초과 - {delay:.0f}초 대기 후 재시도")
                        end = time.monotonic() + delay
                        while time.monotonic() < end and not STOP_EVENT.is_set():
                            time.sleep(min(1.0, max(end - time.monotonic(), 0.05)))
                    else:
                        print(f"  [batch {bi}/{len(batches)}] 키 {prev_label} 할당량 초과 "
                              f"-> 키 {pool[key_idx].label}로 전환")
                    continue
                attempt += 1
                print(f"  [batch {bi}/{len(batches)}] 시도 {attempt}/{args.max_attempts} "
                      f"({entry.label}) 실패: {e}")
                if attempt >= args.max_attempts:
                    break
                # 같은 키를 반복 재시도하지 않고 다음 살아있는 키로 전환 (키 고유 버그/차단 대비)
                nxt = next_alive_index(key_idx + 1)
                if nxt is not None:
                    key_idx = nxt
                time.sleep(min(2 ** attempt, 15))
                continue

            before_count = len(remaining)
            for sid, txt in mapping.items():
                if sid in remaining:
                    remaining[sid].translated = txt
            remaining = {k: v for k, v in remaining.items() if v.translated is None}
            if remaining:
                if len(remaining) >= before_count:
                    no_progress += 1
                    if no_progress >= 3:
                        print(f"  [batch {bi}/{len(batches)}] 3회 연속 진전 없음(모델이 세그먼트를 "
                              f"계속 누락) -> 남은 {len(remaining)}개는 원문 유지하고 다음 배치로")
                        break
                else:
                    no_progress = 0
                print(f"  [batch {bi}/{len(batches)}] 누락 {len(remaining)}개 세그먼트 재요청")

        # 이 배치에서 끝까지 실패/중단된 분은 원문 유지 (문서 손실 방지)
        for s in remaining.values():
            if s.translated is None:
                s.translated = s.text
                s.translation_failed = True
        for s in batch:
            prev_pairs.append((s.text[:300], (s.translated or "")[:300]))
            pages_done.add(s.page)
        prev_pairs = prev_pairs[-12:]
        total_chars_sent += sum(len(s.text) for s in batch)
        done = sum(1 for s in targets if s.translated is not None and s.translated != s.text)
        print(f"  [batch {bi}/{len(batches)}] 완료 (실제 번역 누적 {done}/{len(targets)} 세그먼트)")
        # GUI 진행 표시용 구조화 라인 (사람도 읽을 수 있는 형식)
        pct = 100.0 * bi / max(len(batches), 1)
        print(f"  [진행] batch={bi}/{len(batches)} pages={len(pages_done)}/{total_pages} "
              f"pct={pct:.1f}")

        if aborted:
            break

    # 처리되지 못한 이후 배치들도 원문 유지로 채워둔다 (재구성 단계 안전장치)
    for s in targets:
        if s.translated is None:
            s.translated = s.text
            s.translation_failed = True

    if stopped_by_user:
        status_word = "사용자 중단(진행분까지 저장)"
    elif aborted:
        status_word = "중단됨"
    else:
        status_word = "완료"
    print(f"[3/4] 번역 {status_word}: {len(targets)}개 세그먼트, "
          f"원문 {total_chars_sent:,}자 전송")
    return aborted or stopped_by_user


def mock_translate(segments: list[Segment]) -> None:
    """API 없이 파이프라인(추출/재구성/한글 폰트)을 검증하기 위한 모의 번역."""
    for s in segments:
        if s.needs_translation:
            s.translated = f"[모의 번역] {s.text}"