"""
한 수집 세션(raw_logs/{lab}/{session_ts}/) 안의 장비별 로그를 event_extractor.py로
훑어 DeviceEvent를 세션 전체로 합치고, 서로 다른 장비에 걸쳐 발생한 같은 사건을
하나의 그룹으로 묶는다(예: A 장비의 LLDP timeout과 B 장비의 인터페이스 down이
같은 케이블 단절 사건일 때).

매칭 우선순위:
  1. MAC 주소 일치 — 서로 다른 장비 로그에 같은 MAC(chassisId 등)이 등장하면
     강한 연관 신호이므로 우선 매칭.
  2. 타임스탬프 근접 윈도우(기본 ±2분) — MAC이 없거나 겹치지 않는 이벤트에 대한 fallback.

collector.py의 collect_all()이 이미 session_dir에 장비별 {name}.txt를 모아두므로,
이 모듈은 그 디렉터리 하나만 입력으로 받는다. log_analysis.py/comparator.py 기반
grading 흐름은 건드리지 않는 완전히 별도 파이프라인이다.
"""
import os
import glob
import json
import datetime

from engine.event_extractor import extract_events

DEFAULT_WINDOW_MINUTES = 2

_MONTH_ABBR = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}


def _session_year(session_ts):
    """session_dir 이름(예: "2026-07-23_1725")의 연도를 타임스탬프 파싱 기준 연도로 사용.
    파싱 실패 시 현재 연도로 fallback."""
    try:
        return int(session_ts[:4])
    except (ValueError, TypeError):
        return datetime.date.today().year


def parse_event_ts(event, year):
    """DeviceEvent.timestamp(예: "Jul 23 14:22:36")를 datetime으로 변환. 실패/없음이면 None."""
    if not event.timestamp:
        return None
    try:
        month_str, day_str, time_str = event.timestamp.split()
        month = _MONTH_ABBR.get(month_str)
        if month is None:
            return None
        hh, mm, ss = (int(x) for x in time_str.split(":"))
        return datetime.datetime(year, month, int(day_str), hh, mm, ss)
    except (ValueError, IndexError):
        return None


def collect_session_events(session_dir):
    """session_dir의 장비별 *.txt(매니페스트 제외) 전체에서 DeviceEvent를 뽑아 합친다.
    반환: [DeviceEvent, ...] (파일명순 -> 파일 내 등장순)"""
    events = []
    for path in sorted(glob.glob(os.path.join(session_dir, "*.txt"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        events.extend(extract_events(raw_text, source_file=os.path.basename(path)))
    return events


def correlate_events(events, session_year=None, window_minutes=DEFAULT_WINDOW_MINUTES):
    """
    서로 다른 장비의 DeviceEvent들을 그룹으로 묶는다.
    반환: [{"group_id": int, "method": "mac"|"time_window", "key": str, "events": [DeviceEvent, ...]}]
    method="mac"인 그룹만 만들고 나머지는 각자 단일 이벤트 그룹(method="single")으로 남긴다
    -> 그 후 시간 윈도우 fallback으로 남은 것들끼리 재그룹화.
    """
    session_year = session_year or datetime.date.today().year

    # 1단계: MAC 기준 그룹핑 (서로 다른 장비에 걸쳐 같은 MAC이 등장하는 경우만 그룹으로 인정)
    by_mac = {}
    for e in events:
        if e.mac:
            by_mac.setdefault(e.mac, []).append(e)

    grouped_ids = set()
    groups = []
    for mac, mac_events in by_mac.items():
        devices = {e.device for e in mac_events}
        if len(devices) < 2:
            continue  # 같은 장비 안에서만 반복되는 MAC은 상관관계 신호로 보지 않음
        groups.append({"method": "mac", "key": mac, "events": mac_events})
        grouped_ids.update(id(e) for e in mac_events)

    # 2단계: 남은 이벤트를 타임스탬프순 정렬 후, ±window_minutes 안에 서로 다른 장비의
    # 이벤트가 있으면 같은 그룹으로 묶는 슬라이딩 윈도우(그리디 병합).
    remaining = [e for e in events if id(e) not in grouped_ids]
    dated = [(e, parse_event_ts(e, session_year)) for e in remaining]
    dated.sort(key=lambda pair: (pair[1] is None, pair[1] or datetime.datetime.min))

    window = datetime.timedelta(minutes=window_minutes)
    current_group = []
    for e, ts in dated:
        if ts is None:
            if current_group:
                groups.append(_finalize_time_group(current_group))
                current_group = []
            groups.append({"method": "single", "key": None, "events": [e]})
            continue

        if current_group and (ts - current_group[-1][1]) <= window:
            current_group.append((e, ts))
        else:
            if current_group:
                groups.append(_finalize_time_group(current_group))
            current_group = [(e, ts)]
    if current_group:
        groups.append(_finalize_time_group(current_group))

    for idx, group in enumerate(groups):
        group["group_id"] = idx
    return groups


def _finalize_time_group(dated_pairs):
    events = [e for e, _ in dated_pairs]
    devices = {e.device for e in events}
    if len(devices) < 2:
        return {"method": "single", "key": None, "events": events}
    first_ts = dated_pairs[0][1]
    return {"method": "time_window", "key": first_ts.isoformat(), "events": events}


def build_session_timeline(session_dir, window_minutes=DEFAULT_WINDOW_MINUTES):
    """session_dir 기준으로 이벤트 수집 + 상관관계 그룹핑까지 수행.
    반환: {"session_dir", "event_count", "groups": [...]} (groups의 events는 dict로 직렬화됨)"""
    session_ts = os.path.basename(os.path.normpath(session_dir))
    events = collect_session_events(session_dir)
    groups = correlate_events(events, session_year=_session_year(session_ts), window_minutes=window_minutes)
    return {
        "session_dir": session_dir,
        "event_count": len(events),
        "groups": [
            {"group_id": g["group_id"], "method": g["method"], "key": g["key"],
             "events": [e.to_dict() for e in g["events"]]}
            for g in groups
        ],
    }


def write_session_timeline(session_dir, out_name="_event_timeline.json", window_minutes=DEFAULT_WINDOW_MINUTES):
    """build_session_timeline 결과를 session_dir/out_name에 저장하고 그 dict를 반환."""
    timeline = build_session_timeline(session_dir, window_minutes=window_minutes)
    out_path = os.path.join(session_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    return timeline
