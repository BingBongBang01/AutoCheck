"""
plugins.vendors 패키지를 import하면 등록된 VendorDriver들이 자동으로 로드된다.
(base.py의 register()는 각 드라이버 모듈이 실제로 import되어야 실행되는데,
그동안 base.list_vendors()만 import하는 코드에서는 등록이 누락되는 버그가 있었음 —
여기서 한 번에 전부 import해서 어디서 호출해도 항상 등록된 상태가 되게 함.)
"""
from plugins.vendors import arista  # noqa: F401 (import 자체가 register() 부작용을 일으킴)
from plugins.vendors import cisco  # noqa: F401
