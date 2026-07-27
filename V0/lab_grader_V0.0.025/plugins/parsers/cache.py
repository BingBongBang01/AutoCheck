"""
Parser Cache — 동일한 (vendor, check_id, raw CLI 텍스트) 조합은 두 번 파싱하지 않는다.
raw 텍스트의 sha256 해시를 키로 써서, 파서 함수 자체는 전혀 안 건드리고
plugins/parsers/registry.py의 get_parser() 결과 위에 캐시 레이어만 얹는다.
"""
import hashlib

try:
    from plugins.parsers.registry import get_parser
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from plugins.parsers.registry import get_parser

_CACHE = {}
_STATS = {"hits": 0, "misses": 0}


def _cache_key(vendor, check_id, raw_text):
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    return (vendor.lower(), check_id, digest)


def parse_cached(vendor, check_id, raw_text):
    """등록된 파서로 raw_text를 파싱하되, 동일 입력이면 캐시에서 바로 반환.
    등록된 파서가 없으면 None 리턴(예외 대신 — registry.get_parser와 동일 계약)."""
    key = _cache_key(vendor, check_id, raw_text)
    if key in _CACHE:
        _STATS["hits"] += 1
        return _CACHE[key]

    parser = get_parser(vendor, check_id)
    if parser is None:
        return None

    result = parser(raw_text)
    _CACHE[key] = result
    _STATS["misses"] += 1
    return result


def cache_stats():
    return dict(_STATS)


def clear_cache():
    _CACHE.clear()
    _STATS["hits"] = 0
    _STATS["misses"] = 0


if __name__ == "__main__":
    sample = "VLAN  Name  Status  Ports\n100   USER  active  Et1\n"
    print(parse_cached("arista", "vlan_status", sample))
    print(parse_cached("arista", "vlan_status", sample))  # 캐시 히트
    print("통계:", cache_stats())
