"""
실행 전에 이것부터 돌려서 뭐가 문제인지 확인하는 진단 스크립트.
    python setup_check.py   (Windows)
    python3 setup_check.py  (Mac/Linux)
"""
import sys
import os

print("=" * 50)
print("환경 점검")
print("=" * 50)

print(f"\n[1] 파이썬 버전: {sys.version}")
if sys.version_info < (3, 8):
    print("    [!] 3.8 이상 권장. 너무 오래된 버전이면 문법 오류가 날 수 있음.")
else:
    print("    OK")

print(f"\n[2] 현재 실행 위치: {os.getcwd()}")
expected_files = ["main.py", "unl_parser.py", "engine", "parsers", "labs"]
missing = [f for f in expected_files if not os.path.exists(f)]
if missing:
    print(f"    [!] 이 폴더에 다음이 없음: {missing}")
    print("    -> lab_grader 폴더 '안으로' 들어가서 실행해야 함")
    print("       (Windows: cd 명령으로 폴더 이동 후 실행, 폴더 밖에서 실행하면 이 오류가 남)")
else:
    print("    OK — lab_grader 폴더 안에서 실행 중")

print(f"\n[3] 필수 패키지 확인")
missing_pkgs = []
try:
    import yaml
    print("    yaml(pyyaml): OK")
except ImportError:
    print("    [!] yaml(pyyaml) 없음")
    missing_pkgs.append("pyyaml")

try:
    import netmiko
    print("    netmiko: OK")
except ImportError:
    print("    [!] netmiko 없음 (실제 장비 접속 시에만 필요, discovery/mock 테스트는 없어도 됨)")
    missing_pkgs.append("netmiko")

if missing_pkgs:
    print(f"\n    설치 명령:")
    print(f"    pip install {' '.join(missing_pkgs)} --break-system-packages")
    print(f"    (Windows는 --break-system-packages 빼고: pip install {' '.join(missing_pkgs)})")

print(f"\n[4] 실행 커맨드 확인")
print("    Windows PowerShell/cmd:  python main.py --mock")
print("    Mac/Linux:               python3 main.py --mock")
print("    (실행해도 아무것도 안 뜨면 cmd 창을 직접 열어서 위 명령을 쳐야 함 —")
print("     main.py를 파일 탐색기에서 더블클릭하면 창이 바로 닫혀서 안 보일 수 있음)")

print("\n" + "=" * 50)
if not missing and not missing_pkgs:
    print("환경 이상 없음 — python main.py --mock 실행해보세요")
else:
    print("위 [!] 표시된 항목부터 해결하세요")
print("=" * 50)
