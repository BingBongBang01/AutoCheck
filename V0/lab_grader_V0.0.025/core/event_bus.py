"""
Event Bus — Collector -> Parser -> Rule -> Export -> GUI가 서로를 직접 호출하지 않고
전부 publish(topic, payload)/subscribe(topic, handler)로만 통신하게 한다.

동기 in-process pub/sub이면 충분하다(모든 단계가 같은 프로세스 안에서 돈다 — 프로세스 간
메시징이 필요한 규모가 아님). 한 구독자가 예외를 던져도 다른 구독자/발행 자체가 절대
막히면 안 되므로 handler 호출은 개별적으로 try/except한다.
"""
import threading
import time
from collections import defaultdict, deque

_MAX_HISTORY = 1000


class EventBus:
    def __init__(self, max_history=_MAX_HISTORY):
        self._subscribers = defaultdict(list)   # topic -> [handler]
        self._wildcard = []                       # "*" 구독자(예: GUI 이벤트 피드)
        self._lock = threading.Lock()
        self._history = deque(maxlen=max_history)

    def subscribe(self, topic, handler):
        """topic == '*'이면 모든 이벤트를 받는다(GUI가 개별 topic을 몰라도 되게 하기 위함)."""
        with self._lock:
            if topic == "*":
                self._wildcard.append(handler)
            else:
                self._subscribers[topic].append(handler)
        return handler

    def unsubscribe(self, topic, handler):
        with self._lock:
            bucket = self._wildcard if topic == "*" else self._subscribers.get(topic, [])
            if handler in bucket:
                bucket.remove(handler)

    def publish(self, topic, payload=None):
        event = {"topic": topic, "payload": payload, "ts": time.time()}
        with self._lock:
            self._history.append(event)
            handlers = list(self._subscribers.get(topic, [])) + list(self._wildcard)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass  # 구독자 하나의 실패가 발행자/다른 구독자를 절대 막으면 안 됨
        return event

    def recent(self, limit=200, topic=None):
        with self._lock:
            events = list(self._history)
        if topic:
            events = [e for e in events if e["topic"] == topic]
        return events[-limit:]


bus = EventBus()


if __name__ == "__main__":
    received = []
    bus.subscribe("collector.completed", lambda e: received.append(e))
    bus.subscribe("*", lambda e: print("[wildcard]", e["topic"]))
    bus.publish("collector.completed", {"device_count": 3})
    bus.publish("parser.completed", {"parsed": 10})
    print("받은 이벤트:", received)
    print("최근 이벤트:", bus.recent())
