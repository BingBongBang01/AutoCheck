"""설정 파일의 출처 정책 — 어느 설정을 어디서 읽는가의 단일 출처.

세 종류가 있다:
  - 앱이 소유하는 규칙(APP_OWNED): 번들(config/)에서만 읽는다. 앱을 업데이트하면 바로 새 규칙이 된다.
    사용자 폴더에 사본이 있어도 쓰지 않는다.
  - 사용자가 고칠 수 있는 템플릿(LAYERED): 번들 기본값 위에 사용자 사본(config_root)의 값을 얹는다.
    예전엔 사용자 사본만 읽었는데, 그 사본은 처음 실행할 때 '없을 때 1회' 복사된 것이라 그대로 굳었다 —
    새 버전이 점검 항목에 필드(virtual_na 등)를 더해도 기존 사용자에게는 적용되지 않았다.
  - 사용자 상태(그 밖의 config_root 파일): 사용자 폴더에만 있다(활성 프로파일, 고객사 목록, 화면 설정 등).

병합 규칙(merge_layers): 사용자가 쓴 값이 이기고, 사용자 사본에 없는 키는 번들 기본값으로 채운다.
리스트는 사용자 것을 그대로 쓴다(항목의 선택·순서는 사용자의 것) — 단 식별 키가 정해진 리스트
(점검 항목 check_items 는 name)는 같은 식별자의 기본 항목으로 빠진 필드를 채운다. 사용자가 일부러 지운
항목을 되살리지 않으려고 새 '항목'은 더하지 않는다. 사용자 사본에 이미 있는 값은 기본값이 바뀌어도
그대로다(사본을 지우면 기본값으로 돌아간다).
"""
from pathlib import Path

import yaml

from core.paths import AppPaths

APP_OWNED = ("log_rules.json", "commands_catalog_default.xlsx")
LAYERED = {"inspection_report_template.yaml": {"check_items": "name"}}


def bundled_config(name) -> Path:
    return AppPaths.bundle_root() / "config" / name


def user_config(name) -> Path:
    return AppPaths.config_root() / name


def _read_yaml(path):
    """없거나 깨진 파일은 빈 dict — 설정 파일 하나 때문에 기능이 통째로 멈추지 않게 한다."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_layers(default, override, item_keys=None):
    """default(번들) 위에 override(사용자)를 얹은 새 dict. 입력은 바꾸지 않는다.
    override 의 None 값은 '쓰지 않음'으로 보고 기본값을 쓴다(YAML 에서 값을 비운 키)."""
    item_keys = item_keys or {}
    if not isinstance(default, dict) or not isinstance(override, dict):
        return override if override is not None else default
    merged = dict(default)
    for key, value in override.items():
        if value is None:
            continue
        base = default.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            merged[key] = merge_layers(base, value, item_keys)
        elif key in item_keys and isinstance(base, list) and isinstance(value, list):
            merged[key] = _fill_items(base, value, item_keys[key], item_keys)
        else:
            merged[key] = value
    return merged


def _fill_items(defaults, items, ident, item_keys):
    by_id = {item.get(ident): item for item in defaults if isinstance(item, dict) and item.get(ident) is not None}
    return [merge_layers(by_id[item.get(ident)], item, item_keys)
            if isinstance(item, dict) and item.get(ident) in by_id else item
            for item in items]


def load_layered_yaml(name) -> dict:
    """LAYERED 템플릿 하나를 읽는다 — 번들 기본값 + 사용자 사본."""
    return merge_layers(_read_yaml(bundled_config(name)), _read_yaml(user_config(name)), LAYERED.get(name))
