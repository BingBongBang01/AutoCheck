from abc import ABC, abstractmethod

class AbstractCollector(ABC):
    """데이터 소스(SSH, HTTP, SQL 등)에서 raw 데이터를 수집하는 추상 인터페이스."""
    @abstractmethod
    def collect(self):
        """수집된 결과를 {device: {command_or_key: raw_text_or_data}} 형태로 반환."""
        pass

class AbstractParser(ABC):
    """수집된 raw 데이터를 검증/파싱하여 구조화된 데이터로 변환하는 추상 인터페이스."""
    @abstractmethod
    def parse(self, raw_by_device):
        """
        raw_by_device: AbstractCollector가 반환한 결과
        반환값: {
            "collected_vlan": {...},
            "collected_stp": {...},
            "collected_extended": {...}
        } 형태의 파싱 결과 딕셔너리
        """
        pass
