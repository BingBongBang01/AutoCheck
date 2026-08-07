"""pytest 부트스트랩 — 어느 디렉터리에서 실행해도 engine/core/tools 를 import 할 수 있게 한다.

앱 자체는 항상 프로젝트 루트에서 `python main.py`로 실행되므로 런타임에는 sys.path 문제가
없다. 하지만 pytest는 tests/ 안에서 수집을 시작할 수 있어서, 루트를 명시적으로 넣어 준다.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
