import datetime
import os
import uuid
import yaml


CUSTOMERS_PATH = "config/customers.yaml"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load():
    if not os.path.exists(CUSTOMERS_PATH):
        return []
    with open(CUSTOMERS_PATH, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or []


def save(customers):
    os.makedirs(os.path.dirname(CUSTOMERS_PATH), exist_ok=True)
    with open(CUSTOMERS_PATH, "w", encoding="utf-8") as stream:
        yaml.dump(customers, stream, allow_unicode=True, sort_keys=False)


def ensure_customer(customers, name):
    found = next((item for item in customers if item["name"] == name), None)
    if found:
        return found
    now = _now()
    customer = {"id": uuid.uuid4().hex, "name": name, "created_at": now, "updated_at": now}
    customers.append(customer)
    return customer
