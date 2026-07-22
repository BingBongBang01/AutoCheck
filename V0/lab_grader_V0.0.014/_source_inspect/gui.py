#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess, threading, queue, tempfile, os, sys, json, re, urllib.request
import contextlib, io, traceback, importlib, shutil as _shutil, time, webbrowser, base64
from pathlib import Path

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ENGINE = APP_DIR / "translate_pdf.py"
PORT = 13305


def _read_engine_version() -> str:
    """
    translate_pdf.py 전체를 import하지 않고 __version__ 값만 텍스트로 읽는다.
    (전체 import는 pymupdf 등 무거운 의존성을 미리 로드하게 되어, 그게 없는 환경에서
    GUI 시작 자체가 막힐 위험이 있다 - 버전 표시 하나 때문에 그 위험을 감수할 필요 없음)
    v4.28 모듈화 이후 __version__의 실제 위치가 translate_pdf.py에서 pdf_engine/config.py로
    옮겨졌다(translate_pdf.py는 이제 파사드라 값을 import만 함) - 둘 다 확인한다.
    """
    candidates = [ENGINE, APP_DIR / "pdf_engine" / "config.py"]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            if m:
                return m.group(1)
        except Exception:
            continue
    return "?"


APP_VERSION = _read_engine_version()

# GUI에서 NPU/GPU 체크박스를 런타임별로 활성/비활성화하기 위한 최소 정보.
# translate_pdf.py 전체를 여기서 import하면 pymupdf 등 무거운 의존성이 GUI 시작 시점에
# 미리 로드돼버려서(그게 없는 환경에서 GUI 자체가 안 뜰 위험), 필요한 정보만 가볍게 복제해둔다.
# translate_pdf.py의 RUNTIME_REGISTRY를 바꾸면 이 사전도 함께 갱신해야 한다.
LOCAL_RUNTIMES = {
    "lemonade": {"label": "Lemonade", "supports_npu": True,  "supports_gpu": True},
    "ollama":   {"label": "Ollama",   "supports_npu": False, "supports_gpu": True},
    "lmstudio": {"label": "LM Studio","supports_npu": False, "supports_gpu": True},
}

# 사용자 설정(API 키 등) 저장 위치 - 실행파일 위치와 무관하게 항상 같은 곳을 사용
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PDFTranslaterGUI"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 언어 콤보박스 프리셋 (자유 입력도 가능 - 콤보박스가 readonly가 아님)
SOURCE_LANG_OPTIONS = ["English", "자동 인식", "한국어", "Japanese", "Chinese (Simplified)",
                       "French", "German", "Spanish"]
TARGET_LANG_OPTIONS = ["한국어", "English", "Japanese", "Chinese (Simplified)",
                       "French", "German", "Spanish"]
# GUI 표시용 '자동 인식'을 엔진이 이해하는 sentinel로 변환 (engine.resolve_source_lang과 짝)
AUTO_DETECT_LABEL = "자동 인식"
AUTO_DETECT_SENTINEL = "auto"

# Windows에서 서브프로세스가 부모(windowed EXE)와 별도로 콘솔 창을 새로 띄우는 것을 막는 플래그.
# --windowed로 빌드해도 이 플래그 없이 subprocess를 부르면 자식 프로세스용 콘솔이 반짝 뜰 수 있다.
_NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

def _real_python_candidates():
    found=[]
    if os.name=="nt":
        try:
            p=subprocess.check_output(["py","-3.12","-c","import sys;print(sys.executable)"],
                                      text=True,stderr=subprocess.DEVNULL,creationflags=_NOWIN).strip()
            if p and Path(p).is_file(): found.append(p)
        except Exception: pass
        fallback=Path(os.environ.get("LOCALAPPDATA",""))/"Programs"/"Python"/"Python312"/"python.exe"
        if fallback.is_file() and str(fallback) not in found: found.append(str(fallback))
    if "WindowsApps" not in str(sys.executable) and Path(sys.executable).is_file():
        found.append(sys.executable)
    return found

def _has_pymupdf(python_exe):
    try:
        return subprocess.run([python_exe,"-c","import pymupdf"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,
            creationflags=_NOWIN).returncode==0
    except Exception:return False

def _double_click_bootstrap():
    """
    .py 연결이 WindowsApps Python으로 잡혀 있어도 실제 Python 3.12로 재실행.
    os.execv 대신 subprocess.Popen 후 현재 프로세스를 종료해 Windows Store alias 문제를 피한다.
    """
    if getattr(sys,"frozen",False) or os.name!="nt" or "WindowsApps" not in str(sys.executable):
        return
    script=str(Path(__file__).resolve())
    for pyexe in _real_python_candidates():
        if "WindowsApps" not in pyexe and _has_pymupdf(pyexe):
            subprocess.Popen([pyexe,script,*sys.argv[1:]],cwd=str(Path(__file__).resolve().parent))
            raise SystemExit(0)

_double_click_bootstrap()

PRESETS={2:(450,2,3072),4:(1000,5,4096),8:(2500,16,6144)}
def bits(name):
    for p in (r'(?:^|[-_.])e([2-8])b(?:$|[-_.])',r'(?:^|[-_.])([2-8])b(?:$|[-_.])',r'(?:^|[-_.])q([2-8])(?:[_-]\d+)?(?:$|[-_.])',r'int([2-8])'):
        m=re.search(p,name,re.I)
        if m:return int(m.group(1))
    return 4
def preset(name):
    b=bits(name)
    if b in PRESETS:return PRESETS[b]
    lo=max(x for x in PRESETS if x<b); hi=min(x for x in PRESETS if x>b); t=(b-lo)/(hi-lo)
    return tuple(round(PRESETS[lo][i]+t*(PRESETS[hi][i]-PRESETS[lo][i])) for i in range(3))

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PDF Translater v{APP_VERSION}")
        self.geometry("1000x820")
        self.minsize(850,650)
        self.q=queue.Queue(); self.proc=None; self.keyfile=None; self.engine_completed=False
        self.engine=None; self.start_time=None; self.pct=0.0
        self.inp=tk.StringVar(); self.out=tk.StringVar(); self.pages=tk.StringVar()
        self.src=tk.StringVar(value="English"); self.dst=tk.StringVar(value="한국어")
        self.model_npu=tk.StringVar(value="gemma4-it-e2b-FLM")
        self.model_gpu=tk.StringVar(value="Gemma-3-4b-it-GGUF")
        self.chars=tk.StringVar(); self.segs=tk.StringVar(); self.tokens=tk.StringVar()
        self.use_npu=tk.BooleanVar(value=True); self.api_rows=[]
        self.runtime=tk.StringVar(value="lemonade")
        self.use_gpu=tk.BooleanVar(value=False)
        self.compress=tk.BooleanVar(value=True)
        self.auto_open=tk.BooleanVar(value=True)
        self.last_output_path=None
        self.build()
        if not self.load_config():
            self.add_api("gemini"); self.add_api("anthropic"); self.add_api("openai")
        self.on_runtime_changed()
        self.refresh_models(); self.after(100,self.poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build(self):
        main=ttk.Frame(self,padding=10); main.pack(fill="both",expand=True)
        f=ttk.LabelFrame(main,text="PDF 파일"); f.pack(fill="x")
        for r,(label,var,save) in enumerate((("입력 PDF",self.inp,False),("출력 PDF (비우면 자동 이름)",self.out,True))):
            ttk.Label(f,text=label,width=23).grid(row=r,column=0,padx=5,pady=5,sticky="w")
            ttk.Entry(f,textvariable=var).grid(row=r,column=1,padx=5,pady=5,sticky="ew")
            ttk.Button(f,text="찾기",command=lambda v=var,s=save:self.pick(v,s)).grid(row=r,column=2,padx=5)
            if r==0:
                ttk.Button(f,text="폴더 열기",command=self.open_input_folder).grid(row=r,column=3,padx=5)
        f.columnconfigure(1,weight=1)

        o=ttk.LabelFrame(main,text="기본 설정"); o.pack(fill="x",pady=7)
        ttk.Label(o,text="페이지 범위").grid(row=0,column=0,padx=5,pady=6)
        ttk.Entry(o,textvariable=self.pages,width=20).grid(row=0,column=1,padx=5)
        ttk.Label(o,text="원문 언어").grid(row=0,column=2,padx=5,pady=6)
        ttk.Combobox(o,textvariable=self.src,values=SOURCE_LANG_OPTIONS,width=18).grid(row=0,column=3,padx=5)
        ttk.Label(o,text="번역 언어").grid(row=0,column=4,padx=5,pady=6)
        ttk.Combobox(o,textvariable=self.dst,values=TARGET_LANG_OPTIONS,width=18).grid(row=0,column=5,padx=5)
        ttk.Checkbutton(o,text="번역 후 PDF 압축",variable=self.compress).grid(row=1,column=0,columnspan=2,padx=5,pady=(0,6),sticky="w")
        ttk.Checkbutton(o,text="완료 시 결과 PDF 자동 열기",variable=self.auto_open).grid(row=1,column=2,columnspan=3,padx=5,pady=(0,6),sticky="w")

        a=ttk.LabelFrame(main,text="API 키 — 체크된 API만 사용"); a.pack(fill="x")
        self.api_frame=ttk.Frame(a); self.api_frame.pack(fill="x")
        api_buttons=ttk.Frame(a); api_buttons.pack(fill="x",padx=5,pady=4)
        ttk.Button(api_buttons,text="+ API 추가",command=lambda:self.add_api("gemini")).pack(side="left")

        s=ttk.LabelFrame(main,text="설치 / 환경 설정"); s.pack(fill="x",pady=7)
        install_buttons=ttk.Frame(s); install_buttons.pack(fill="x",padx=5,pady=4)
        ttk.Button(install_buttons,text="Python 3.12 설치",command=self.install_python).pack(side="left")
        ttk.Button(install_buttons,text="Lemonade 설치",command=self.install_lemonade).pack(side="left",padx=6)
        ttk.Button(install_buttons,text="패키지(pip) 설치",command=self.install_packages).pack(side="left")
        ttk.Separator(install_buttons,orient="vertical").pack(side="left",fill="y",padx=8)
        ttk.Button(install_buttons,text="전체 자동 설치",command=self.install_prerequisites).pack(side="left")
        ttk.Label(install_buttons,text="(Python+Lemonade+패키지 한번에)",
                 foreground="#666666").pack(side="left",padx=6)
        install_buttons2=ttk.Frame(s); install_buttons2.pack(fill="x",padx=5,pady=(0,4))
        ttk.Button(install_buttons2,text="manga-ocr 설치(선택)",command=self.install_manga_ocr).pack(side="left")
        ttk.Label(install_buttons2,
                 text="일본어 만화 OCR 전용 - 설치 시 용량 ~1GB, 인식 정확도 크게 향상. 미설치 시 Tesseract만 사용(자동)",
                 foreground="#666666").pack(side="left",padx=6)

        n=ttk.LabelFrame(main,text="로컬 AI 설정 (NPU/GPU)"); n.pack(fill="x",pady=7)
        ttk.Label(n,text="런타임").grid(row=0,column=0,padx=5,pady=5)
        self.runtime_box=ttk.Combobox(n,textvariable=self.runtime,values=list(LOCAL_RUNTIMES),
                                      state="readonly",width=12)
        self.runtime_box.grid(row=0,column=1,padx=5)
        self.runtime_box.bind("<<ComboboxSelected>>",self.on_runtime_changed)
        self.npu_check=ttk.Checkbutton(n,text="NPU 사용",variable=self.use_npu,command=self.on_device_toggled)
        self.npu_check.grid(row=0,column=2,padx=5)
        self.gpu_check=ttk.Checkbutton(n,text="GPU 사용",variable=self.use_gpu,command=self.on_device_toggled)
        self.gpu_check.grid(row=0,column=3,padx=5)
        # NPU/GPU는 서로 다른 모델(recipe)이 필요하므로(FLM=NPU전용, GGUF=GPU용) 콤보박스를 분리한다.
        # 체크 안 된 장치의 콤보박스는 비활성화해서, 안 쓰는 장치의 모델을 실수로 잘못 고르지 않게 한다.
        ttk.Label(n,text="NPU 모델").grid(row=0,column=4,padx=5)
        self.npu_models=ttk.Combobox(n,textvariable=self.model_npu,state="readonly",width=24)
        self.npu_models.grid(row=0,column=5,padx=5)
        self.npu_models.bind("<<ComboboxSelected>>",self.model_changed)
        ttk.Label(n,text="GPU 모델").grid(row=1,column=4,padx=5,pady=(0,5))
        self.gpu_models=ttk.Combobox(n,textvariable=self.model_gpu,state="readonly",width=24)
        self.gpu_models.grid(row=1,column=5,padx=5,pady=(0,5))
        self.gpu_models.bind("<<ComboboxSelected>>",self.model_changed)
        ttk.Button(n,text="모델 새로고침",command=self.refresh_models).grid(row=0,column=6,padx=5)
        for i,(label,var) in enumerate((("배치 문자 수",self.chars),("세그먼트 수",self.segs),("max_tokens",self.tokens))):
            ttk.Label(n,text=label).grid(row=2,column=i*2,padx=5,pady=7)
            ttk.Entry(n,textvariable=var,width=15).grid(row=2,column=i*2+1,padx=5)

        self.status=tk.StringVar(value="대기 중")
        ttk.Label(main,textvariable=self.status).pack(anchor="w")
        self.prog_info=tk.StringVar(value="")
        ttk.Label(main,textvariable=self.prog_info,foreground="#555555").pack(anchor="w")
        self.progress=ttk.Progressbar(main,maximum=100); self.progress.pack(fill="x",pady=5)
        buttons=ttk.Frame(main); buttons.pack(fill="x")
        self.startbtn=ttk.Button(buttons,text="번역 시작",command=self.start); self.startbtn.pack(side="left")
        self.stopbtn=ttk.Button(buttons,text="중단 (진행분까지 저장)",command=self.stop,state="disabled")
        self.stopbtn.pack(side="left",padx=5)
        self.openresultbtn=ttk.Button(buttons,text="결과 PDF 열기",command=self.open_last_output,state="disabled")
        self.openresultbtn.pack(side="left",padx=5)
        ttk.Button(buttons,text="로그 지우기",command=lambda:self.log.delete("1.0","end")).pack(side="right")
        self.log=tk.Text(main,height=25,wrap="word"); self.log.pack(fill="both",expand=True,pady=7)

    def add_api(self, provider):
        row=ttk.Frame(self.api_frame); row.pack(fill="x",padx=5,pady=2)
        on=tk.BooleanVar(value=False); pv=tk.StringVar(value=provider); key=tk.StringVar()
        ttk.Checkbutton(row,variable=on).pack(side="left")
        ttk.Combobox(row,textvariable=pv,values=("gemini","anthropic","openai"),state="readonly",width=12).pack(side="left",padx=4)
        ttk.Entry(row,textvariable=key,show="●").pack(side="left",fill="x",expand=True)
        item=[row,on,pv,key]
        ttk.Button(row,text="삭제",command=lambda:self.remove_api(item)).pack(side="left",padx=4)
        self.api_rows.append(item)
    def remove_api(self,item):
        item[0].destroy()
        if item in self.api_rows:self.api_rows.remove(item)

    # ------------------------------------------------------------------
    # 필수사항 설치: Python 3.12 -> Lemonade Server -> pip requirements 순차 진행
    # ------------------------------------------------------------------
    def _log(self,msg): self.q.put(("LOG",msg if msg.endswith("\n") else msg+"\n"))

    def _python312_ok(self):
        return any("WindowsApps" not in p for p in _real_python_candidates())

    def _lemonade_ok(self):
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/api/v1/models",timeout=2):return True
        except Exception:pass
        bin_dir=Path(os.environ.get("LOCALAPPDATA",""))/"lemonade_server"/"bin"
        return (bin_dir/"LemonadeServer.exe").is_file() or bool(_shutil.which("LemonadeServer") or _shutil.which("lemonade-server"))

    def _run_stream(self,cmd):
        """명령을 실행하고 출력을 로그 큐로 스트리밍. 종료코드 반환."""
        flags=0x08000000 if os.name=="nt" else 0
        p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                           encoding="utf-8",errors="replace",creationflags=flags)
        for line in p.stdout:self.q.put(("LOG",line))
        return p.wait()

    # Windows용 바이너리(exe 설치 프로그램)가 제공되는 마지막 3.12.x 버전.
    # 3.12.11부터는 "source-only" 보안 릴리스라 exe 설치 프로그램 자체가 없다 - 이후
    # 버전으로 URL을 만들면 다운로드가 404로 실패한다.
    PYTHON312_INSTALLER_VERSION = "3.12.10"
    PYTHON312_INSTALLER_URL = (
        f"https://www.python.org/ftp/python/{PYTHON312_INSTALLER_VERSION}/"
        f"python-{PYTHON312_INSTALLER_VERSION}-amd64.exe"
    )

    def _install_python_worker(self) -> bool:
        """
        Python 3.12 설치. 반환값: 성공(이미 설치돼 있던 경우 포함) 여부.

        중요한 두 가지 문제를 피해야 한다:
        1) winget으로 설치할 때 소스를 명시 안 하면 'winget'(공식 python.org 빌드)과
           'msstore'(마이크로소프트 스토어의 껍데기 앱, PATH/환경변수 문제가 많아 부적합)
           소스가 같은 ID로 겹쳐서 스토어 버전이 설치될 위험이 있다 -> --source winget 명시.
        2) winget 자체가 없는 PC(구버전 Windows, App Installer 미설치 등)에서는 지금까지
           자동 설치가 전혀 안 되고 브라우저만 열어줬다 -> winget이 없거나 winget 설치가
           실패하면, python.org 공식 설치 프로그램을 직접 다운로드해 무인 설치하는 것으로
           확실히 폴백한다(스토어를 절대 거치지 않음).
        """
        if self._python312_ok():
            self._log("[Python] Python 3.12: 이미 설치됨 - 건너뜀")
            return True

        if _shutil.which("winget"):
            self._log("[Python] Python 3.12 미설치 -> winget으로 설치 시도 (공식 winget 소스 지정)")
            code=self._run_stream(["winget","install","-e","--id","Python.Python.3.12",
                                   "--source","winget",
                                   "--accept-source-agreements","--accept-package-agreements",
                                   "--silent"])
            if code==0 and self._python312_ok():
                self._log("[Python] Python 3.12 설치 완료 (winget)")
                return True
            self._log(f"[Python][경고] winget 설치가 완료되지 않음 (코드 {code}). "
                      "공식 인스톨러를 직접 다운로드해 설치를 시도합니다...")
        else:
            self._log("[Python] winget이 없음 -> python.org 공식 인스톨러를 직접 다운로드해 설치합니다.")

        # winget이 없거나 winget 설치가 실패한 경우: python.org 공식 exe를 직접 받아 무인 설치.
        # 마이크로소프트 스토어를 아예 거치지 않으므로, 스토어판의 PATH/환경변수 문제를
        # 원천적으로 피할 수 있다.
        try:
            dst=Path(tempfile.gettempdir())/f"python-{self.PYTHON312_INSTALLER_VERSION}-amd64.exe"
            self._log(f"[Python] 공식 인스톨러 다운로드 중: {self.PYTHON312_INSTALLER_URL}")
            urllib.request.urlretrieve(self.PYTHON312_INSTALLER_URL, dst)
            self._log("[Python] 인스톨러 실행 중 (무인 설치, PATH 자동 등록)...")
            # InstallAllUsers=0: 관리자 권한 없이도 설치 가능(사용자 단위).
            # PrependPath=1: 이번 설치를 PATH 맨 앞에 등록 -> 'python' 명령이 바로 이걸 가리킴.
            # Include_launcher=1: py 런처(py.exe)도 함께 설치 (이 프로젝트의 build_exe.bat이
            #   'py -3.12'로 이 파이썬을 찾으므로 반드시 필요).
            code=self._run_stream([str(dst), "/quiet", "InstallAllUsers=0", "PrependPath=1",
                                   "Include_launcher=1", "Include_test=0"])
            try:
                dst.unlink()
            except Exception:
                pass
            if code==0 and self._python312_ok():
                self._log("[Python] Python 3.12 설치 완료 (공식 인스톨러)")
                return True
            self._log(f"[Python][경고] 공식 인스톨러 무인 설치가 완료되지 않음 (코드 {code}).")
        except Exception as e:
            self._log(f"[Python][경고] 공식 인스톨러 다운로드/실행 실패: {e}")

        self._log("[Python][경고] 자동 설치에 실패했습니다. python.org 다운로드 페이지를 엽니다 - "
                  "수동 설치 시 'Add python.exe to PATH' 체크를 꼭 하세요. "
                  "주의: Windows 검색창에서 'python'을 쳤을 때 뜨는 '마이크로소프트 스토어에서 "
                  "다운로드'는 선택하지 마세요 - 그 버전은 PATH/환경변수 문제가 많아 이 "
                  "프로그램과 호환되지 않습니다. 이미 스토어판을 설치했다면, 설정 > 앱 > "
                  "고급 앱 설정 > 앱 실행 별칭에서 'python.exe'/'python3.exe' 별칭을 꺼서 "
                  "비활성화한 뒤 위 공식 인스톨러로 다시 설치하세요.")
        webbrowser.open("https://www.python.org/downloads/release/python-31210/")
        return False

    def install_python(self):
        """'Python 3.12 설치' 버튼 - 이것만 단독 실행."""
        self.status.set("Python 3.12 확인/설치 중...")
        def work():
            try:
                ok=self._install_python_worker()
                self.after(0,lambda:self.status.set(
                    "Python 3.12 준비 완료" if ok else "Python 수동 설치 필요"))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.after(0,lambda:self.status.set("Python 설치 중 오류"))
        threading.Thread(target=work,daemon=True).start()

    def _install_lemonade_worker(self) -> bool:
        """Lemonade Server 설치. 반환값: 성공(이미 설치돼 있던 경우 포함) 여부."""
        if self._lemonade_ok():
            self._log("[Lemonade] Lemonade Server: 이미 설치됨 - 건너뜀")
            return True
        self._log("[Lemonade] Lemonade Server 미설치 -> GitHub 최신 릴리스 조회 중...")
        try:
            with urllib.request.urlopen(
                "https://api.github.com/repos/lemonade-sdk/lemonade/releases/latest",
                timeout=15) as r:
                rel=json.load(r)
            assets=rel.get("assets",[])
            cand=[a for a in assets if a.get("name","").lower().endswith(".exe")]
            cand.sort(key=lambda a:sum(k in a.get("name","").lower()
                                       for k in ("win","setup","installer","server")),reverse=True)
            if not cand:
                raise RuntimeError("릴리스에서 .exe 인스톨러를 찾지 못함")
            url=cand[0]["browser_download_url"]; name=cand[0]["name"]
            dst=Path(tempfile.gettempdir())/name
            self._log(f"[Lemonade] 다운로드: {name} ({cand[0].get('size',0)//1048576}MB)...")
            urllib.request.urlretrieve(url,dst)
            self._log("[Lemonade] 인스톨러 실행 - 설치 창의 안내를 따라 설치를 완료하세요.")
            subprocess.Popen([str(dst)])
            self._log("[Lemonade] 설치가 끝나면 이 버튼을 다시 눌러 확인하세요.")
            return False  # 설치 프로그램은 백그라운드로 뜨므로, 이 시점엔 아직 완료 확인 불가
        except Exception as e:
            self._log(f"[Lemonade][경고] 자동 다운로드 실패({e}). 릴리스 페이지를 엽니다.")
            webbrowser.open("https://github.com/lemonade-sdk/lemonade/releases/latest")
            return False

    def install_lemonade(self):
        """'Lemonade 설치' 버튼 - 이것만 단독 실행."""
        self.status.set("Lemonade Server 확인/설치 중...")
        def work():
            try:
                ok=self._install_lemonade_worker()
                self.after(0,lambda:self.status.set(
                    "Lemonade 준비 완료" if ok else "Lemonade 설치 진행 중/확인 필요 - 로그 참고"))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.after(0,lambda:self.status.set("Lemonade 설치 중 오류"))
        threading.Thread(target=work,daemon=True).start()

    def _install_packages_worker(self) -> bool:
        """pip requirements.txt 설치. 반환값: 성공 여부."""
        req=APP_DIR/"requirements.txt"
        if not req.exists():
            self._log(f"[패키지][오류] requirements.txt를 찾을 수 없습니다: {req}")
            return False
        pyexe=next((p for p in _real_python_candidates() if "WindowsApps" not in p),None)
        if not pyexe:
            self._log("[패키지][오류] 실제 Python 실행 파일을 찾지 못했습니다. "
                      "'Python 3.12 설치'를 먼저 실행하세요.")
            return False
        self._log(f"[패키지] pip 요구사항 설치 ({pyexe})...")
        code=self._run_stream([pyexe,"-m","pip","install","--upgrade","-r",str(req)])
        self._log("[패키지] pip 요구사항 "+("설치 완료" if code==0 else f"설치 실패 (코드 {code})"))
        return code==0

    def _install_manga_ocr_worker(self) -> bool:
        """manga-ocr(+PyTorch) 설치. 선택 사항이라 별도 버튼으로 분리돼 있다 - 반환값: 성공 여부."""
        pyexe=next((p for p in _real_python_candidates() if "WindowsApps" not in p),None)
        if not pyexe:
            self._log("[manga-ocr][오류] 실제 Python 실행 파일을 찾지 못했습니다. "
                      "'Python 3.12 설치'를 먼저 실행하세요.")
            return False
        self._log(f"[manga-ocr] 설치 시작 ({pyexe}) - PyTorch 포함, 용량이 크고(~1GB) "
                  "시간이 오래 걸릴 수 있습니다...")
        code=self._run_stream([pyexe,"-m","pip","install","--upgrade","manga-ocr"])
        self._log("[manga-ocr] 설치 "+("완료" if code==0 else f"실패 (코드 {code})"))
        return code==0

    def install_manga_ocr(self):
        """'manga-ocr 설치(선택)' 버튼 - 이것만 단독 실행.
        일본어 만화 전용 OCR(Tesseract보다 정확도가 높음)이지만 PyTorch 의존성 때문에
        용량이 크고(~1GB) 다른 언어는 지원하지 않는다(하이브리드: 일본어만 이걸 쓰고
        나머지 언어는 그대로 Tesseract). 그래서 기본 설치에 포함하지 않고 선택 사항으로
        분리했다 - 미설치 상태에서도 프로그램은 정상 동작한다(자동으로 Tesseract만 사용)."""
        self.status.set("manga-ocr 설치 중(용량이 커 시간이 걸릴 수 있음)...")
        def work():
            try:
                ok=self._install_manga_ocr_worker()
                self.after(0,lambda:self.status.set(
                    "manga-ocr 설치 완료" if ok else "manga-ocr 설치 실패 - 로그 확인"))
                if ok:
                    self.after(0,lambda:messagebox.showinfo(
                        "설치 결과","manga-ocr 설치가 완료되었습니다. 다음 번역부터 "
                        "일본어 OCR에 자동으로 사용됩니다(다른 언어는 계속 Tesseract 사용)."))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.after(0,lambda:self.status.set("manga-ocr 설치 중 오류"))
        threading.Thread(target=work,daemon=True).start()

    def install_packages(self):
        """'패키지(pip) 설치' 버튼 - 이것만 단독 실행."""
        self.status.set("필수 패키지(pip) 설치 중...")
        def work():
            try:
                ok=self._install_packages_worker()
                self.after(0,lambda:self.status.set(
                    "패키지 설치 완료" if ok else "패키지 설치 실패 - 로그 확인"))
                if ok:
                    self.after(0,lambda:messagebox.showinfo(
                        "설치 결과","패키지 설치가 완료되었습니다. GUI를 다시 실행하세요."))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.after(0,lambda:self.status.set("패키지 설치 중 오류"))
        threading.Thread(target=work,daemon=True).start()

    def install_prerequisites(self):
        """'전체 자동 설치' 버튼 - Python -> Lemonade -> 패키지 순서로 전부 실행하는 통합 래퍼.
        개별 설치 함수(_install_*_worker)를 그대로 재사용하므로 로직 중복이 없다."""
        self.status.set("전체 자동 설치 중... (Python -> Lemonade -> 패키지)")
        def work():
            try:
                if not self._install_python_worker():
                    self.after(0,lambda:self.status.set("Python 수동 설치 필요 - 완료 후 다시 눌러주세요"))
                    return
                self._install_lemonade_worker()
                self._install_packages_worker()
                self.after(0,lambda:self.status.set("전체 자동 설치 완료 - 로그 확인"))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.after(0,lambda:self.status.set("전체 자동 설치 중 오류"))
        threading.Thread(target=work,daemon=True).start()

    # ------------------------------------------------------------------
    # 설정(API 키 등) 저장/복원 - 앱 재시작해도 이어서 사용 가능하게
    # 주의: 키는 base64로 살짝 가려둘 뿐 암호화가 아니다(로컬 개인 PC 사용 전제).
    # ------------------------------------------------------------------
    def save_config(self):
        try:
            data={
                "api_rows":[{"provider":pv.get(),"on":on.get(),
                            "key":base64.b64encode(key.get().encode("utf-8")).decode("ascii")}
                           for _,on,pv,key in self.api_rows],
                "src":self.src.get(),"dst":self.dst.get(),
                "model_npu":self.model_npu.get(),"model_gpu":self.model_gpu.get(),
                "use_npu":self.use_npu.get(),
                "runtime":self.runtime.get(),"use_gpu":self.use_gpu.get(),
                "compress":self.compress.get(),"auto_open":self.auto_open.get(),
                "chars":self.chars.get(),"segs":self.segs.get(),"tokens":self.tokens.get(),
            }
            CONFIG_DIR.mkdir(parents=True,exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception as e:
            print(f"[설정 저장 실패] {e}",file=sys.stderr)

    def load_config(self) -> bool:
        """저장된 설정이 있으면 복원. 복원했으면 True (기본 API 행 3개를 추가하지 않도록)."""
        if not CONFIG_PATH.exists():
            return False
        try:
            data=json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[설정 로드 실패] {e}",file=sys.stderr)
            return False
        rows=data.get("api_rows") or []
        for r in rows:
            self.add_api(r.get("provider","gemini"))
            item=self.api_rows[-1]
            item[1].set(bool(r.get("on",False)))
            try:
                item[3].set(base64.b64decode(r.get("key","").encode("ascii")).decode("utf-8"))
            except Exception:
                pass
        if data.get("src"):self.src.set(data["src"])
        if data.get("dst"):self.dst.set(data["dst"])
        if data.get("model_npu"):self.model_npu.set(data["model_npu"])
        if data.get("model_gpu"):self.model_gpu.set(data["model_gpu"])
        if "use_npu" in data:self.use_npu.set(data["use_npu"])
        if data.get("runtime") in LOCAL_RUNTIMES:self.runtime.set(data["runtime"])
        if "use_gpu" in data:self.use_gpu.set(data["use_gpu"])
        if "compress" in data:self.compress.set(data["compress"])
        if "auto_open" in data:self.auto_open.set(data["auto_open"])
        if data.get("chars"):self.chars.set(data["chars"])
        if data.get("segs"):self.segs.set(data["segs"])
        if data.get("tokens"):self.tokens.set(data["tokens"])
        return bool(rows)

    def on_close(self):
        self.save_config()
        self.destroy()

    # install_requirements(구 "요구사항 설치" 버튼)는 install_packages()로 통합됨.

    def pick(self,var,save):
        p=filedialog.asksaveasfilename(defaultextension=".pdf",filetypes=[("PDF","*.pdf")]) if save else filedialog.askopenfilename(filetypes=[("PDF","*.pdf")])
        if p:var.set(p)

    @staticmethod
    def _open_path(path):
        """OS 기본 프로그램으로 파일/폴더 열기 (Windows 전용 os.startfile, 이 앱은 Windows 대상)."""
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            # Windows가 아닌 환경(개발/테스트용) 대비 폴백
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])

    def open_input_folder(self):
        """'폴더 열기' 버튼: 입력 PDF가 있는 폴더를 탐색기로 연다 (파일을 선택한 상태로)."""
        p=self.inp.get().strip()
        if not p:
            return messagebox.showerror("오류","먼저 입력 PDF를 선택하세요.")
        path=Path(p)
        if not path.exists():
            return messagebox.showerror("오류",f"파일을 찾을 수 없습니다:\n{path}")
        try:
            if os.name=="nt":
                subprocess.Popen(["explorer","/select,",str(path)])
            else:
                self._open_path(path.parent)
        except Exception as e:
            messagebox.showerror("오류",f"폴더를 여는 중 오류: {e}")

    def open_last_output(self):
        """'결과 열기' 버튼: 마지막으로 저장된 번역 결과 PDF를 기본 프로그램으로 연다."""
        if not self.last_output_path or not Path(self.last_output_path).exists():
            return messagebox.showinfo("안내","아직 번역 결과 파일이 없습니다.")
        self._open_path(self.last_output_path)

    # 런타임별 기본 포트/API 경로 (translate_pdf.RUNTIME_REGISTRY와 동기화)
    _RUNTIME_PORTS = {"lemonade": (13305, "/api/v1/models"), "ollama": (11434, "/v1/models"),
                      "lmstudio": (1234, "/v1/models")}

    def on_runtime_changed(self,*_):
        """런타임 콤보박스가 바뀌면 그 런타임이 지원하는 장치만 체크 가능하게 하고,
        미지원 장치는 체크 해제 + 비활성화한다."""
        caps=LOCAL_RUNTIMES.get(self.runtime.get(),{"supports_npu":False,"supports_gpu":False})
        if caps["supports_npu"]:
            self.npu_check["state"]="normal"
        else:
            self.use_npu.set(False); self.npu_check["state"]="disabled"
        if caps["supports_gpu"]:
            self.gpu_check["state"]="normal"
        else:
            self.use_gpu.set(False); self.gpu_check["state"]="disabled"
        self.on_device_toggled()
        self.refresh_models()

    def on_device_toggled(self):
        """NPU/GPU 체크 상태에 맞춰 해당 모델 콤보박스만 활성화한다.
        (체크 안 된 장치의 모델을 실수로 잘못 고르는 걸 막기 위함 - 예전엔 콤보박스가
        하나뿐이라 GPU만 체크해도 NPU 전용 모델이 그대로 남아있어 GPU 체크가 무시되는
        버그가 있었다.)"""
        self.npu_models["state"]="readonly" if self.use_npu.get() else "disabled"
        self.gpu_models["state"]="readonly" if self.use_gpu.get() else "disabled"

    def refresh_models(self):
        rt=self.runtime.get()
        label=LOCAL_RUNTIMES.get(rt,{}).get("label",rt)
        port,path=self._RUNTIME_PORTS.get(rt,(PORT,"/api/v1/models"))
        self.status.set(f"{label} 모델 조회 중...")
        def work():
            found=[]
            try:
                with urllib.request.urlopen(f"http://localhost:{port}{path}",timeout=3) as r:d=json.load(r)
                items=d.get("data",[]) if isinstance(d,dict) else d
                for x in items:
                    m=(x.get("id") or x.get("model") or x.get("name")) if isinstance(x,dict) else str(x)
                    if m and m not in found:found.append(m)
            except Exception:pass
            self.after(0,lambda:self.set_models(found,label))
        threading.Thread(target=work,daemon=True).start()

    @staticmethod
    def _is_npu_model(name:str)->bool:
        """모델명 접미사로 실제 실행 장치 추정 (translate_pdf.model_recipe_device와 동기화).
        '-FLM' 접미사 = NPU 전용(FLM 레시피), 그 외(-GGUF 등) = GPU/CPU(llamacpp 레시피)."""
        n=name.lower()
        return n.endswith("-flm") or "-flm-" in n

    def set_models(self,found,label="Lemonade"):
        if not found:found=["gemma4-it-e2b-FLM","gemma4-it-e4b-FLM","Gemma-3-4b-it-GGUF","qwen3-it-4b-FLM"]
        npu_found=[m for m in found if self._is_npu_model(m)]
        gpu_found=[m for m in found if not self._is_npu_model(m)]
        self.npu_models["values"]=npu_found or ["gemma4-it-e2b-FLM"]
        self.gpu_models["values"]=gpu_found or ["Gemma-3-4b-it-GGUF"]
        if npu_found and self.model_npu.get() not in npu_found:self.model_npu.set(npu_found[0])
        if gpu_found and self.model_gpu.get() not in gpu_found:self.model_gpu.set(gpu_found[0])
        self.model_changed(); self.status.set("대기 중" if found else f"{label} 서버 연결 실패")

    def model_changed(self,*_):
        # 배치/토큰 프리셋은 실제로 쓰일 모델(체크된 장치 우선, NPU 우선) 기준으로 계산
        ref_model=self.model_npu.get() if self.use_npu.get() else self.model_gpu.get()
        c,s,t=preset(ref_model); self.chars.set(str(c)); self.segs.set(str(s)); self.tokens.set(str(t))

    def start(self):
        if not self.inp.get():
            return messagebox.showerror("오류","입력 PDF를 선택하세요.")
        try:
            int(self.chars.get()); int(self.segs.get()); int(self.tokens.get())
        except ValueError:
            return messagebox.showerror("오류","배치/세그먼트/max_tokens는 숫자여야 합니다.")

        self.save_config()  # 입력한 API 키/설정을 즉시 저장 (다음 실행 시 이어서 사용)

        keys=[]
        for _,on,pv,key in self.api_rows:
            if on.get() and key.get().strip():
                keys.append(f"{pv.get()}:{key.get().strip()}")
        devices=[]
        if self.use_npu.get():devices.append("npu")
        if self.use_gpu.get():devices.append("gpu")
        if not keys and not devices:
            return messagebox.showerror("오류","사용할 API 또는 로컬 NPU/GPU를 선택하세요.")

        fd,self.keyfile=tempfile.mkstemp(prefix="pdftranslator_",suffix=".txt")
        os.close(fd)
        Path(self.keyfile).write_text("\n".join(keys),encoding="utf-8")

        argv=[str(ENGINE),self.inp.get(),
              "--source-lang",self.src.get(),"--target-lang",self.dst.get(),
              "--batch-chars",self.chars.get(),"--batch-segs",self.segs.get(),
              "--max-tokens",self.tokens.get(),"--api-key-file",self.keyfile,
              "--model-select-timeout","0","--local-runtime",self.runtime.get()]
        if self.use_npu.get(): argv+=["--model-local-npu",self.model_npu.get()]
        if self.use_gpu.get(): argv+=["--model-local-gpu",self.model_gpu.get()]
        if self.pages.get().strip(): argv+=["--pages",self.pages.get().strip()]
        if self.out.get().strip(): argv+=["-o",self.out.get().strip()]
        if devices: argv+=["--local-device",",".join(devices)]
        if not self.compress.get(): argv+=["--no-compress"]

        self.log.delete("1.0","end")
        self.openresultbtn["state"]="disabled"
        self.last_output_path=None
        self.engine_completed=False
        self.progress["value"]=0
        self.pct=0.0
        self.start_time=time.time()
        self.prog_info.set(f"시작: {time.strftime('%H:%M:%S')}")
        self.startbtn["state"]="disabled"
        self.stopbtn["state"]="normal"
        self.status.set("번역 시작")

        class QueueWriter(io.TextIOBase):
            def __init__(self,q): self.q=q
            def write(self,s):
                if s: self.q.put(("LOG",s))
                return len(s)
            def flush(self): pass

        def run():
            old_argv=sys.argv[:]
            old_env={k:os.environ.get(k) for k in ("PYTHONIOENCODING","PYTHONUTF8")}
            try:
                sys.argv=argv
                # EXE에서는 translate_pdf.py를 외부 프로세스로 재실행하지 않고
                # 동일 프로세스에 번들된 모듈로 import하여 main()을 호출한다.
                engine=importlib.import_module("translate_pdf")
                self.engine=engine
                if hasattr(engine,"reset_stop"): engine.reset_stop()
                writer=QueueWriter(self.q)
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    try:
                        engine.main()
                        code=0
                    except SystemExit as e:
                        code=e.code if isinstance(e.code,int) else (0 if e.code is None else 1)
                        if e.code and not isinstance(e.code,int):
                            print(e.code)
                self.q.put(("DONE",code))
            except Exception:
                self.q.put(("LOG",traceback.format_exc()))
                self.q.put(("ERR","번역 엔진 실행 중 예외가 발생했습니다. 로그를 확인하세요."))
            finally:
                sys.argv=old_argv
                for k,v in old_env.items():
                    if v is None: os.environ.pop(k,None)
                    else: os.environ[k]=v
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        try:
            while True:
                typ,val=self.q.get_nowait()
                if typ=="LOG":
                    self.log.insert("end",val);self.log.see("end")
                    fm=re.search(r"\[최종파일\]\s*(.+)",val)
                    if fm:
                        self.last_output_path=fm.group(1).strip()
                        self.openresultbtn["state"]="normal"
                    # 엔진의 구조화 진행 라인: [진행] batch=i/N pages=p/P pct=xx.x
                    pm=re.search(r"\[진행\] batch=(\d+)/(\d+) pages=(\d+)/(\d+) pct=([\d.]+)",val)
                    if pm:
                        bi,bn,pd,pt,pct=int(pm.group(1)),int(pm.group(2)),int(pm.group(3)),int(pm.group(4)),float(pm.group(5))
                        self.pct=pct
                        self.progress["value"]=pct
                        self.status.set(f"번역 중 - 전체 {pt}페이지 중 {pd}페이지 진행 (배치 {bi}/{bn}, {pct:.1f}%)")
                    else:
                        m=re.search(r"\[batch (\d+)/(\d+)\]",val)
                        if m and self.pct==0:
                            self.progress["value"]=10+80*int(m.group(1))/max(1,int(m.group(2)))
                        elif "[1/4]" in val:self.progress["value"]=5
                        elif "[3/4]" in val:self.progress["value"]=max(self.progress["value"],92)
                        elif "[4/4]" in val:
                            self.progress["value"]=100
                            self.engine_completed=True
                else:
                    self.cleanup();self.startbtn["state"]="normal";self.stopbtn["state"]="disabled"
                    self.start_time=None
                    if typ=="DONE":
                        # 출력 PDF 재구성 완료 로그가 확인되면 후처리의 비핵심 종료코드보다 실제 결과를 우선한다.
                        effective_code = 0 if self.engine_completed else val
                        self.status.set("완료" if effective_code==0 else f"오류 종료 ({effective_code})")
                        if effective_code==0:
                            self.progress["value"]=100
                            if self.auto_open.get() and self.last_output_path:
                                self.open_last_output()
                        messagebox.showinfo("실행 종료","번역 및 PDF 저장이 완료되었습니다." if effective_code==0 else f"프로세스 종료 코드: {effective_code}")
                    else:self.status.set("오류");messagebox.showerror("오류",val)
        except queue.Empty:pass
        # 경과/예상 시간 갱신 (1초 단위 체감)
        if self.start_time:
            el=time.time()-self.start_time
            h,rem=divmod(int(el),3600); m,s=divmod(rem,60)
            eta="계산 중" if self.pct<=0 else time.strftime("%H:%M:%S",time.gmtime(el/self.pct*(100-self.pct)))
            self.prog_info.set(
                f"시작: {time.strftime('%H:%M:%S',time.localtime(self.start_time))}"
                f" | 경과: {h:02d}:{m:02d}:{s:02d}"
                f" | 예상 남은시간: {eta}"
                f" | 진행률: {self.pct:.1f}%")
        self.after(100,self.poll)

    def cleanup(self):
        if self.keyfile:
            try:os.remove(self.keyfile)
            except:pass
            self.keyfile=None
    def stop(self):
        """우아한 중단: 엔진에 중단 신호 -> 현재 배치까지 번역 후 나머지는 원문 유지 저장.
        저장된 파일명의 _untranslated 구간으로 나중에 이어서-번역 가능."""
        eng=self.engine
        if eng is None:
            eng=sys.modules.get("translate_pdf")
        if eng is not None and hasattr(eng,"request_stop"):
            eng.request_stop()
            self.stopbtn["state"]="disabled"
            self.status.set("중단 요청됨 - 현재 배치 완료 후 진행분까지 저장합니다...")
            self.log.insert("end","[GUI] 중단 요청 - 현재 처리 중인 배치가 끝나면 "
                                  "번역된 부분까지 PDF로 저장됩니다.\n")
            self.log.see("end")
        else:
            messagebox.showinfo("중지 안내","실행 중인 번역 작업이 없습니다.")

if __name__=="__main__":
    App().mainloop()
