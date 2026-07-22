"""build_exe.bat 전용 헬퍼: __version__ 값만 안전하게 추출해 표준출력으로 인쇄.
배치파일 안에 복잡한 정규식/따옴표를 직접 넣으면 파싱 오류 위험이 있어 별도 파일로 분리했다.
v4.28 모듈화 이후 __version__의 실제 위치가 translate_pdf.py에서 pdf_engine/config.py로
옮겨졌다(translate_pdf.py는 이제 파사드라 값을 import만 함) - 둘 다 확인한다."""
import re
import sys
from pathlib import Path

here = Path(__file__).parent
for candidate in (here / "translate_pdf.py", here / "pdf_engine" / "config.py"):
    if not candidate.exists():
        continue
    text = candidate.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if m:
        print(m.group(1))
        sys.exit(0)
print("ERROR: __version__ not found", file=sys.stderr)
sys.exit(1)
