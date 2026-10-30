"""
AutoCheck PyInstaller EXE 원클릭 빌드 스크립트.

사전 준비(빌드 도구 설치):
    pip install -r requirements-build.txt

실행방법:
    python build_exe.py

실행 결과:
    dist/AutoCheck/ 폴더 내에 AutoCheck.exe 및 자산 파일들이 패키징됩니다. EXE 사용자 안내(실행방법.txt)와
    라이선스(LICENSE)는 AutoCheck.exe 옆에 복사합니다 — 번들 안(_internal)에 두면 사용자가 찾지 못한다.
"""
import os
import shutil
import subprocess
import sys

USER_GUIDE = "실행방법.txt"
LICENSE_FILE = "LICENSE"

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def build():
    print("=== 1. AutoCheck EXE 빌드 사전 작업 ===")
    
    # 아이콘 변환 (app_icon.jpg -> app_icon.ico). .ico 는 저장소에 들어 있으므로 jpg 가 더 새로울 때만
    # 다시 만든다 — 매번 만들면 빌드할 때마다 소스 트리가 바뀐 것으로 나온다.
    ico_path = os.path.join("web_ui", "icons", "app_icon.ico")
    jpg_path = os.path.join("web_ui", "icons", "app_icon.jpg")
    if os.path.exists(jpg_path) and (not os.path.exists(ico_path)
                                     or os.path.getmtime(ico_path) < os.path.getmtime(jpg_path)):
        try:
            from PIL import Image
            img = Image.open(jpg_path)
            img.save(ico_path, format="ICO", sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])
            print(f"[OK] 앱 아이콘(.ico) 생성 완료: {ico_path}")
        except Exception as e:
            print(f"[!] 아이콘 변환 경고: {e}")
            
    print("\n=== 2. PyInstaller 빌드 시작 ===")
    cmd = [sys.executable, "-m", "PyInstaller", "AutoCheck.spec", "--noconfirm"]
    res = subprocess.run(cmd)
    
    if res.returncode == 0:
        dist_dir = os.path.join("dist", "AutoCheck")
        exe_path = os.path.join(dist_dir, "AutoCheck.exe")
        shutil.copy2(USER_GUIDE, os.path.join(dist_dir, USER_GUIDE))
        shutil.copy2(LICENSE_FILE, os.path.join(dist_dir, LICENSE_FILE))
        print(f"\n[OK] EXE 빌드 성공!")
        print(f"-> 실행 파일 위치: {os.path.abspath(exe_path)}")
    else:
        print("\n[ERROR] PyInstaller 빌드 중 오류 발생.")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
