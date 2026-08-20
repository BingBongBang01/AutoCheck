"""
설정 YAML을 안전하게 저장하는 공용 함수.

같은 폴더에 임시 파일로 먼저 다 쓰고 `os.replace()`로 갈아끼운다. 같은 파일시스템 안의
rename은 원자적이라, 쓰는 도중에 프로세스가 죽어도 파일은 "원본 그대로" 아니면
"새 내용 전부" 둘 중 하나만 남는다.

이게 필요한 이유:
`device_inventory.yaml`은 장비·IP·계정의 유일한 원본인데, 장비 목록 화면의 자동저장이
타이핑 중 0.8초마다, 연결 확인 결과가 돌아올 때마다 이 파일을 다시 쓴다. 앱에서 가장
자주 쓰이는 파일이다. 예전 방식(`open(path, "w")`)은 여는 순간 파일을 0바이트로 비우고
나서 내용을 채우기 때문에, 그 사이에 창을 닫거나 프로세스가 죽으면 장비 목록이 잘린 채
남는다. 잘린 YAML도 문법적으로는 멀쩡해서 그냥 로드되고, 사용자는 오류 한 줄 없이
장비 40대가 13대로 줄어든 걸 나중에야 발견하게 된다 — 실제로 재현해서 확인한 시나리오다.

`core/storage_service.py`와 `engine/profile_manager.py`는 이미 같은 방식을 쓰고 있었고,
프로젝트 설정 YAML만 빠져 있었다.
"""
import os
import tempfile

import yaml


def write_text_atomic(path, text, encoding="utf-8"):
    """텍스트를 원자적으로 저장한다. 실패하면 기존 파일은 손대지 않은 채로 남는다."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())   # 내용이 디스크에 닿은 뒤에 교체해야 정전에도 안전
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)    # 교체에 성공했으면 이미 없음 — 실패했을 때의 뒷정리


def dump_yaml_atomic(data, path):
    """YAML 직렬화를 먼저 끝내고 나서 파일을 건드린다.

    직렬화 도중 예외가 나도(순환 참조 등) 기존 파일은 그대로다 —
    스트림에 바로 dump하면 반쯤 쓰다 죽어서 파일이 깨진다.
    """
    text = yaml.dump(data, allow_unicode=True, sort_keys=False)
    write_text_atomic(path, text)
