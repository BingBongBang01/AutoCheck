"""점검 로그 읽기/판정 결과 캐시 — OPTIMIZATION_PLAN 3-2.

같은 원본 로그가 한 사용자 흐름에서 4~5회 다시 읽히고 다시 파싱된다:

    api/terminal_inspection_api.py  점검 직후 run_analysis() 로 전 파일 파싱,
                                    그 **직후** 같은 파일들을 다시 파싱해 경고 목록을 만든다
    api/dashboard_api.py            대시보드 집계
    api/report_api.py               보고서 탭 / Findings
    api/log_analysis_run_api.py     AI 분석용 문맥 추출(내부에서 또 analyze_text)

실측(장비 30대 합성 코퍼스): 파싱 84 ms/패스, 읽기 0.38 ms/패스. 즉 비용은 거의 전부
파싱이고, 대시보드 전체 비용의 99% 가 여기다. 2단계 줄 단위 메모가 이미 2.5~3.3배
줄였지만 '같은 파일을 처음부터 다시 훑는' 구조는 그대로 남아 있다.

캐시 키는 **파일 신원**(경로, st_mtime_ns, 크기)이다:
  * 점검이 새 로그를 쓰면 mtime/크기가 바뀌어 자동으로 무효가 된다 — 점검 직후 대시보드가
    이전 회차 수치를 보여주는 일이 없다. 이게 이 캐시에서 가장 중요한 성질이다.
  * mtime 을 초 단위로 쓰면 같은 초에 두 번 쓰인 파일을 놓칠 수 있어 **나노초**(st_mtime_ns)를
    쓰고 크기까지 키에 넣는다. 파일시스템 타임스탬프 해상도가 아주 거친 환경(FAT32 2초 등)이면
    크기가 같은 덮어쓰기를 놓칠 수 있다 — 그런 매체에 data/ 를 두는 것은 상정하지 않는다.

무효화를 신경 쓰지 않아도 되도록 이 모듈은 **읽기 전용 조회 경로만** 캐시한다. 파일을 쓰는
쪽(수집/분석 결과 저장)은 캐시를 건드리지 않는다 — 다음 조회에서 키가 달라져 알아서 갱신된다.
"""
import os
from collections import OrderedDict

from core.text_io import read_log_text

__all__ = [
    "cached_log_text", "cached_findings", "invalidate", "clear", "stats",
    "MAX_TEXT_ENTRIES", "MAX_TEXT_BYTES", "MAX_FINDING_ENTRIES",
]

# 상한. 로그 전문은 크므로 개수와 총 바이트 양쪽으로 묶는다.
MAX_TEXT_ENTRIES = 200
MAX_TEXT_BYTES = 64 * 1024 * 1024      # 64 MB
MAX_FINDING_ENTRIES = 400              # findings 는 전문보다 훨씬 작다

_text_cache = OrderedDict()             # key -> str
_text_bytes = 0
_findings_cache = OrderedDict()         # key -> list[dict]
_hits = {"text": 0, "findings": 0}
_misses = {"text": 0, "findings": 0}


def _identity(path):
    """(정규화 경로, st_mtime_ns, size). 파일이 없으면 None(캐시하지 않는다)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (os.path.normcase(os.path.abspath(path)), stat.st_mtime_ns, stat.st_size)


def _remember_text(key, text):
    global _text_bytes
    size = len(text)
    _text_cache[key] = text
    _text_bytes += size
    # 상한을 넘으면 가장 오래 안 쓴 것부터 버린다(LRU).
    while _text_cache and (len(_text_cache) > MAX_TEXT_ENTRIES or _text_bytes > MAX_TEXT_BYTES):
        _dropped_key, dropped = _text_cache.popitem(last=False)
        _text_bytes -= len(dropped)


def cached_log_text(path):
    """로그 전문을 읽어 돌려준다. 같은 파일이 바뀌지 않았으면 재사용한다."""
    key = _identity(path)
    if key is None:
        return read_log_text(path)      # 사라진 파일 — 예전과 같은 예외를 내게 둔다

    if key in _text_cache:
        _hits["text"] += 1
        _text_cache.move_to_end(key)
        return _text_cache[key]

    _misses["text"] += 1
    text = read_log_text(path)
    _remember_text(key, text)
    return text


def _copy_findings(findings):
    """호출자가 캐시된 객체를 오염시키지 못하게 얕은 복사본을 준다.

    지금의 호출부는 모두 읽기 전용이지만, 캐시가 생기면 "내가 받은 dict 에 필드를 하나
    붙이자"는 변경이 자연스러워지고 그 순간 다음 호출자가 오염된 값을 받는다. 리스트와
    각 dict 를 새로 만들어 그 사고를 막는다. 중첩된 block 리스트는 읽기만 하므로 공유한다
    (전문 줄들을 매번 복사하면 캐시의 이득이 깎인다).
    """
    return [dict(finding) for finding in findings]


def cached_findings(path, *, text=None):
    """파일 하나의 analyze_text() 결과. 파일이 바뀌지 않았으면 재파싱하지 않는다.

    text: 이미 읽어 둔 전문이 있으면 넘긴다(중복 읽기 방지). 캐시 미스일 때만 쓰인다.
    """
    from engine.log_analysis import analyze_text

    key = _identity(path)
    if key is None:
        return analyze_text(text if text is not None else read_log_text(path))

    if key in _findings_cache:
        _hits["findings"] += 1
        _findings_cache.move_to_end(key)
        return _copy_findings(_findings_cache[key])

    _misses["findings"] += 1
    findings = analyze_text(text if text is not None else cached_log_text(path))
    _findings_cache[key] = findings
    while len(_findings_cache) > MAX_FINDING_ENTRIES:
        _findings_cache.popitem(last=False)
    return _copy_findings(findings)


def invalidate(path):
    """특정 파일의 캐시를 버린다. 파일 신원으로 키를 잡으므로 보통은 부를 필요가 없다 —
    파일을 덮어쓰면 다음 조회에서 키가 달라져 자동으로 갱신된다."""
    global _text_bytes
    prefix = os.path.normcase(os.path.abspath(path))
    for cache in (_text_cache, _findings_cache):
        for key in [k for k in cache if k[0] == prefix]:
            value = cache.pop(key)
            if cache is _text_cache:
                _text_bytes -= len(value)


def clear():
    """전부 비운다 — 테스트/진단용."""
    global _text_bytes
    _text_cache.clear()
    _findings_cache.clear()
    _text_bytes = 0
    for counter in (_hits, _misses):
        for name in counter:
            counter[name] = 0


def stats():
    """캐시 상태 — 상한에 닿았는지, 히트율이 기대만큼인지 확인용."""
    return {
        "text_entries": len(_text_cache),
        "text_bytes": _text_bytes,
        "findings_entries": len(_findings_cache),
        "text_hits": _hits["text"], "text_misses": _misses["text"],
        "findings_hits": _hits["findings"], "findings_misses": _misses["findings"],
        "max_text_entries": MAX_TEXT_ENTRIES,
        "max_text_bytes": MAX_TEXT_BYTES,
        "max_findings_entries": MAX_FINDING_ENTRIES,
    }
