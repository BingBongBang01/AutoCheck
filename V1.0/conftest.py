"""pytest 부트스트랩.

1. 어느 디렉터리에서 실행해도 engine/core/tools 를 import 할 수 있게 한다.
   앱 자체는 항상 프로젝트 루트에서 `python main.py`로 실행되므로 런타임에는 sys.path 문제가
   없다. 하지만 pytest는 tests/ 안에서 수집을 시작할 수 있어서, 루트를 명시적으로 넣어 준다.

2. 사용자 데이터 폴더를 테스트 전용 임시 폴더로 돌린다(환경변수 AUTOCHECK_HOME).
   몇몇 모듈은 import 하는 순간 경로를 상수로 고정한다(engine/project_manager.LABS_DIR 등).
   그래서 테스트가 앱 모듈을 import 하기만 해도 실제 Documents\\AutoCheck 가 만들어지고 설정
   템플릿이 복사됐다. 이 설정은 어떤 앱 모듈보다 먼저 일어나야 하므로 여기 최상단에서 한다.
   자식 프로세스로 도는 테스트(test_startup_imports)도 이 환경변수를 물려받는다.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 개발자가 평소에 AUTOCHECK_HOME 을 실제 데이터 폴더로 잡아 두었더라도 테스트는 늘 새 임시 폴더를 쓴다.
_TEST_HOME = Path(tempfile.mkdtemp(prefix="autocheck_test_home_"))
os.environ["AUTOCHECK_HOME"] = str(_TEST_HOME)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_HOME, ignore_errors=True)
