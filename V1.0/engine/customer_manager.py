import os
import uuid
import yaml

from core.atomic_io import dump_yaml_atomic
from core.paths import AppPaths
from core.utils import format_iso

CUSTOMERS_PATH = str(AppPaths.config_root() / "customers.yaml")


def load():
    if not os.path.exists(CUSTOMERS_PATH):
        return []
    with open(CUSTOMERS_PATH, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or []


def save(customers):
    # 고객사 목록은 프로파일 트리의 뿌리다 — 쓰다 죽으면 목록이 잘린 채 남지 않게 원자적으로 쓴다.
    dump_yaml_atomic(customers, CUSTOMERS_PATH)


def ensure_customer(customers, name):
    """이름으로 고객사를 찾고 없으면 만든다. 대소문자만 다른 이름은 같은 고객사다 — data 폴더가
    Windows 에서 하나이기 때문이다(따로 만들면 두 고객사가 한 폴더를 함께 쓴다)."""
    found = next((item for item in customers if item["name"].casefold() == name.casefold()), None)
    if found:
        return found
    now = format_iso()
    customer = {"id": uuid.uuid4().hex, "name": name, "created_at": now, "updated_at": now}
    customers.append(customer)
    return customer
