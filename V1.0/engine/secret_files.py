"""디스크에 남은 평문 비밀값을 봉인한다(core/secret_vault — IMPROVEMENT_PLAN 4-1).

부르는 곳은 둘이다: 앱 시작 때 한 번(engine/migration_manager 의 업그레이드 1 — 이전 버전이 평문으로 써 둔 것),
그리고 밖에서 들어온 파일(프로젝트 zip 불러오기 — '계정 정보 포함'으로 내보낸 zip 은 평문이다). 그 뒤로는 쓰는 쪽
(장비 목록 save_inventory, AI 설정 _save_ai_config)이 봉인해서 쓴다.

대상은 비밀값을 담는, 이름이 정해진 파일뿐이다. 사용자가 내보낸 엑셀·CSV 는 사용자의 파일이라 건드리지 않는다.
"""
import json
from pathlib import Path

import yaml

from core.atomic_io import dump_yaml_atomic, write_text_atomic
from core.secret_redaction import SECRET_FIELD_NAMES
from core.secret_vault import seal_fields

__all__ = ["SECRET_FILE_NAMES", "SECRET_KEYS", "seal_secret_file", "seal_secret_files"]

# device_inventory.yaml: 장비 비밀번호·붙여넣은 개인키·passphrase·기본 비밀번호
# ip_allocation.yaml:    레거시 장비 계정(allocations·default_credentials)
# credential.json:       예전 버전이 프로파일마다 만든 기본 계정 사본(지금은 만들지도 읽지도 않는다)
# ai_config.yaml:        클라우드 AI API 키
# alarm.yaml:            알림 채널의 웹훅 주소(경로가 곧 토큰이다)·SMTP 비밀번호(IMPROVEMENT_PLAN 4-4)
SECRET_FILE_NAMES = ("device_inventory.yaml", "ip_allocation.yaml", "credential.json", "ai_config.yaml", "alarm.yaml")
SECRET_KEYS = frozenset(SECRET_FIELD_NAMES | {"api_key", "webhook_url"})


def seal_secret_file(path) -> int:
    """파일 하나의 평문 비밀값을 봉인한다(원자적 쓰기). 반환: 새로 봉인한 값 수 — 0 이면 파일을 쓰지 않는다.
    읽을 수 없거나 형식이 깨진 파일은 건너뛴다(0) — 이전 단계가 사용자 데이터를 망가뜨리면 안 된다."""
    path = Path(path)
    is_json = path.suffix.lower() == ".json"
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if is_json else yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return 0
    sealed, count = seal_fields(data, SECRET_KEYS)
    if not count:
        return 0
    if is_json:
        write_text_atomic(path, json.dumps(sealed, ensure_ascii=False, indent=2))
    else:
        dump_yaml_atomic(sealed, path)
    return count


def seal_secret_files(root) -> dict:
    """root 아래의 비밀값 파일을 모두 봉인한다. 반환: {"files": 바꾼 파일 수, "values": 봉인한 값 수}."""
    root = Path(root)
    files = values = 0
    if not root.is_dir():
        return {"files": 0, "values": 0}
    for name in SECRET_FILE_NAMES:
        for path in sorted(root.rglob(name)):
            if not path.is_file():
                continue
            count = seal_secret_file(path)
            if count:
                files += 1
                values += count
    return {"files": files, "values": values}
