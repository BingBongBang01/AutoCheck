"""
LAB 자동채점 프로그램 — 데스크톱 UI (Tkinter, 표준 라이브러리만 사용 = 추가 설치 불필요)
머티리얼 디자인풍 색상/카드 스타일 적용 (벤치마킹: NetBox/LibreNMS/Grafana 톤 참고)

실행: python gui.py

주의: 이 샌드박스 환경(디스플레이 없음)에서는 실제 창을 띄워 테스트할 수 없음.
      본인 노트북(Windows, 디스플레이 있음)에서 실행해야 창이 뜸.
"""
import io
import os
import sys
import contextlib
import json
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, scrolledtext

try:
    from engine import project_manager as pm
    from engine import command_catalog as cc
    from gui_widgets import ScrollableFrame
    from gui_theme import COLOR, apply_theme_colors
except ModuleNotFoundError as error:
    if error.name == "yaml":
        messagebox.showerror("필수 패키지 없음", "PyYAML이 설치되지 않았습니다.\nPowerShell에서 다음 명령을 실행하세요:\n\npy.exe -m pip install pyyaml\n\n설치 후 gui.py를 다시 실행하세요.")
        raise SystemExit(1)
    raise

class LabGraderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LAB 자동채점 프로그램")
        self.root.geometry("980x640")
        self.root.configure(bg=COLOR["page"])

        self.theme_mode = "system"
        if os.path.exists("config/ui.yaml"):
            import yaml
            with open("config/ui.yaml", encoding="utf-8") as file:
                self.theme_mode = (yaml.safe_load(file) or {}).get("theme", "system")
        if self.theme_mode == "dark" or (self.theme_mode == "system" and self._system_is_dark()):
            apply_theme_colors("dark")

        self._setup_styles()

        self.active_project = pm.get_active_project()
        self.nav_buttons = {}
        self.current_nav = "dashboard"

        self._build_top_bar()
        self._build_body()
        self.refresh_projects()
        self.show_dashboard()

    def _system_is_dark(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except (ImportError, OSError):
            return False

    # ---------- 스타일 정의 ----------
    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=COLOR["page"])
        style.configure("Surface.TFrame", background=COLOR["surface"])
        style.configure("Sidebar.TFrame", background=COLOR["surface_alt"])
        style.configure("TopBar.TFrame", background=COLOR["surface_alt"])

        style.configure("TLabel", background=COLOR["page"], foreground=COLOR["text"], font=("Segoe UI", 10))
        style.configure("Surface.TLabel", background=COLOR["surface"], foreground=COLOR["text"], font=("Segoe UI", 10))
        style.configure("Sidebar.TLabel", background=COLOR["surface_alt"], foreground=COLOR["text"], font=("Segoe UI", 10))
        style.configure("Heading.TLabel", background=COLOR["page"], foreground=COLOR["text"], font=("Segoe UI", 13, "bold"))
        style.configure("Muted.TLabel", background=COLOR["surface"], foreground=COLOR["text_secondary"], font=("Segoe UI", 9))
        style.configure("MetricValue.TLabel", background=COLOR["surface"], font=("Segoe UI", 20, "bold"))
        style.configure("MetricLabel.TLabel", background=COLOR["surface"], foreground=COLOR["text_secondary"], font=("Segoe UI", 9))

        style.configure("Nav.TButton", background=COLOR["surface_alt"], foreground=COLOR["text_secondary"],
                         borderwidth=0, focusthickness=0, font=("Segoe UI", 10), padding=(10, 8), anchor="w")
        style.map("Nav.TButton", background=[("active", COLOR["border"])])

        style.configure("NavActive.TButton", background=COLOR["accent_bg"], foreground=COLOR["accent"],
                         borderwidth=0, focusthickness=0, font=("Segoe UI", 10, "bold"), padding=(10, 8), anchor="w")
        style.map("NavActive.TButton", background=[("active", COLOR["accent_bg"])])

        style.configure("Accent.TButton", background=COLOR["accent"], foreground="white",
                         borderwidth=0, font=("Segoe UI", 9, "bold"), padding=(10, 6))
        style.map("Accent.TButton", background=[("active", "#0C447C")])

        style.configure("TButton", font=("Segoe UI", 9), padding=(8, 5))
        style.configure("TEntry", fieldbackground=COLOR["surface"], foreground=COLOR["text"])
        style.configure("TCheckbutton", background=COLOR["surface"], foreground=COLOR["text"])
        style.map("TCheckbutton", background=[("active", COLOR["surface"])], foreground=[("disabled", COLOR["text_muted"])])
        style.configure("TRadiobutton", background=COLOR["surface"], foreground=COLOR["text"])
        style.configure("TNotebook", background=COLOR["page"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLOR["surface_alt"], foreground=COLOR["text"], padding=(10, 5))
        style.map("TNotebook.Tab", background=[("selected", COLOR["accent_bg"])] , foreground=[("selected", COLOR["accent"])])

        style.configure("Success.Horizontal.TProgressbar", background=COLOR["success"], troughcolor=COLOR["surface_alt"])
        style.configure("Danger.Horizontal.TProgressbar", background=COLOR["danger"], troughcolor=COLOR["surface_alt"])
        style.configure("Neutral.Horizontal.TProgressbar", background=COLOR["text_muted"], troughcolor=COLOR["surface_alt"])

        style.configure("Treeview", background=COLOR["surface"], fieldbackground=COLOR["surface"],
                         foreground=COLOR["text"], rowheight=26, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=COLOR["surface_alt"], foreground=COLOR["text_secondary"],
                         font=("Segoe UI", 9, "bold"))

    def show_settings(self):
        self._set_active_nav("settings")
        self._clear_content()
        ttk.Label(self.content, text="프로그램 설정", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        card = self._card(self.content, fill="x")
        pad = ttk.Frame(card, style="Surface.TFrame", padding=14)
        pad.pack(fill="x")
        ttk.Label(pad, text="화면 테마", style="Surface.TLabel").pack(anchor="w")
        self.theme_var = tk.StringVar(value=getattr(self, "theme_mode", "system"))
        for value, label in (("system", "시스템"), ("light", "라이트"), ("dark", "다크")):
            ttk.Radiobutton(pad, text=label, value=value, variable=self.theme_var, command=self.apply_theme).pack(side="left", padx=(0, 12), pady=8)
        ttk.Label(pad, text="시스템 테마는 Windows 설정의 앱 모드를 기준으로 적용합니다.", style="Muted.TLabel").pack(anchor="w")

    def apply_theme(self):
        import yaml
        mode = self.theme_var.get()
        self.theme_mode = mode
        with open("config/ui.yaml", "w", encoding="utf-8") as file:
            yaml.dump({"theme": mode}, file, allow_unicode=True, sort_keys=False)
        if mode == "dark" or (mode == "system" and self._system_is_dark()):
            COLOR.update({"page": "#202124", "surface": "#292A2D", "surface_alt": "#303134", "border": "#4A4B4F", "text": "#F1F3F4", "text_secondary": "#BDC1C6", "text_muted": "#9AA0A6"})
        else:
            COLOR.update({"page": "#F7F7F5", "surface": "#FFFFFF", "surface_alt": "#F1F1EF", "border": "#E0E0DC", "text": "#1A1A18", "text_secondary": "#5F5E5A", "text_muted": "#9A998F"})
        self.root.configure(bg=COLOR["page"])
        self._setup_styles()
        self.refresh_widget_colors(self.root)
        self.show_settings()

    def refresh_widget_colors(self, widget):
        for child in widget.winfo_children():
            try:
                if isinstance(child, (tk.Frame, tk.Canvas)):
                    child.configure(bg=COLOR["page"])
                elif isinstance(child, tk.Label):
                    child.configure(bg=COLOR["surface"], fg=COLOR["text"])
            except tk.TclError:
                pass
            self.refresh_widget_colors(child)

    # ---------- 상단 바 (프로젝트 선택) ----------
    def _build_top_bar(self):
        bar = tk.Frame(self.root, bg=COLOR["surface_alt"], height=48)
        bar.pack(side="top", fill="x")
        inner = ttk.Frame(bar, style="TopBar.TFrame", padding=(12, 8))
        inner.pack(fill="x")

        ttk.Label(inner, text="프로젝트", style="Sidebar.TLabel",
                  font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))

        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(inner, textvariable=self.project_var, state="readonly", width=28)
        self.project_combo.pack(side="left")
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_selected)

        ttk.Button(inner, text="+ 새 프로젝트", style="Accent.TButton", command=self.on_new_project).pack(side="left", padx=(10, 4))
        ttk.Button(inner, text="이름 변경", command=self.on_rename_project).pack(side="left", padx=4)
        ttk.Button(inner, text="삭제", command=self.on_delete_project).pack(side="left", padx=4)

    # ---------- 본문 (좌측 네비 + 우측 컨텐츠) ----------
    def _build_body(self):
        body = tk.Frame(self.root, bg=COLOR["page"])
        body.pack(side="top", fill="both", expand=True)

        nav = tk.Frame(body, bg=COLOR["surface_alt"], width=170)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        nav_inner = ttk.Frame(nav, style="Sidebar.TFrame", padding=10)
        nav_inner.pack(fill="both", expand=True)

        nav_items = [
            ("dashboard", "대시보드", self.show_dashboard),
            ("discovery", "Discovery", self.show_discovery),
            ("catalog", "커맨드 카탈로그", self.show_catalog),
            ("collection", "수집", self.show_collection),
            ("grade", "채점 실행", self.show_grade),
            ("history", "이력", self.show_history),
            ("report", "보고서", self.show_report),
            ("connection", "연결 설정", self.show_connection_settings),
            ("inventory", "장비·SSH 설정", self.show_inventory),
            ("terminal", "SSH 터미널", self.show_terminal),
            ("settings", "프로그램 설정", self.show_settings),
            ("ai", "AI 설정", self.show_ai_settings),
        ]
        for key, label, cmd in nav_items:
            btn = ttk.Button(nav_inner, text=label, style="Nav.TButton", command=cmd)
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        outer_content = tk.Frame(body, bg=COLOR["page"])
        outer_content.pack(side="left", fill="both", expand=True)
        self.content_canvas = tk.Canvas(outer_content, bg=COLOR["page"], highlightthickness=0)
        content_scrollbar = ttk.Scrollbar(outer_content, orient="vertical", command=self.content_canvas.yview)
        self.content = ttk.Frame(self.content_canvas, padding=16)
        content_window = self.content_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda event: self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all")))
        self.content_canvas.bind("<Configure>", lambda event: self.content_canvas.itemconfigure(content_window, width=event.width))
        self.content_canvas.configure(yscrollcommand=content_scrollbar.set)
        self.content_canvas.pack(side="left", fill="both", expand=True)
        content_scrollbar.pack(side="right", fill="y")
        self.content_canvas.bind_all("<MouseWheel>", self._scroll_content)
        self.content_canvas.bind_all("<Button-4>", self._scroll_content)
        self.content_canvas.bind_all("<Button-5>", self._scroll_content)

    def _scroll_content(self, event):
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -int(event.delta / 120)
        self.content_canvas.yview_scroll(delta, "units")

    def _set_active_nav(self, key):
        self.current_nav = key
        for k, btn in self.nav_buttons.items():
            btn.configure(style="NavActive.TButton" if k == key else "Nav.TButton")

    def _clear_content(self):
        for widget in self.content.winfo_children():
            widget.destroy()

    def _card(self, parent, **kwargs):
        """카드형 컨테이너 — 흰 배경 + 옅은 테두리, 머티리얼 카드 느낌."""
        outer = tk.Frame(parent, bg=COLOR["border"])
        inner = tk.Frame(outer, bg=COLOR["surface"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        outer.pack(**kwargs)
        return inner

    def _metric_card(self, parent, label, value, color=None):
        card = self._card(parent, side="left", fill="both", expand=True, padx=4)
        pad = ttk.Frame(card, style="Surface.TFrame", padding=12)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text=label, style="MetricLabel.TLabel").pack(anchor="w")
        val_style = "MetricValue.TLabel"
        val_label = ttk.Label(pad, text=str(value), style=val_style)
        if color:
            val_label.configure(foreground=color)
        val_label.pack(anchor="w")
        return card

    # ---------- 프로젝트 관리 ----------
    def refresh_projects(self):
        projects = pm.list_projects()
        self.project_map = {p["display_name"]: p["id"] for p in projects}
        self.project_combo["values"] = list(self.project_map.keys())

        active_id = pm.get_active_project()
        active_name = next((p["display_name"] for p in projects if p["id"] == active_id), None)
        if active_name:
            self.project_var.set(active_name)
        elif projects:
            self.project_var.set(projects[0]["display_name"])
            pm.set_active_project(projects[0]["id"])

    def on_project_selected(self, event=None):
        name = self.project_var.get()
        project_id = self.project_map.get(name)
        if project_id:
            pm.set_active_project(project_id)
            self.show_dashboard()

    def on_new_project(self):
        name = simpledialog.askstring("새 프로젝트", "프로젝트 이름:")
        if not name:
            return
        try:
            new_id = pm.create_project(name)
            pm.set_active_project(new_id)
            self.refresh_projects()
            messagebox.showinfo("완료", f"'{name}' 프로젝트가 생성됨")
        except ValueError as e:
            messagebox.showerror("오류", str(e))

    def on_rename_project(self):
        name = self.project_var.get()
        project_id = self.project_map.get(name)
        if not project_id:
            return
        new_name = simpledialog.askstring("이름 변경", "새 이름:", initialvalue=name)
        if not new_name:
            return
        pm.rename_project(project_id, new_name)
        self.refresh_projects()

    def on_delete_project(self):
        name = self.project_var.get()
        project_id = self.project_map.get(name)
        if not project_id:
            return
        if not messagebox.askyesno("삭제 확인", f"'{name}' 프로젝트를 삭제하시겠습니까?\n(폴더 전체가 삭제되며 되돌릴 수 없음)"):
            return
        pm.delete_project(project_id)
        self.refresh_projects()

    # ---------- 대시보드 ----------
    def show_dashboard(self):
        self._set_active_nav("dashboard")
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text=f"대시보드", style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.content, text=project_id or "", foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 12))

        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None
        if project_id:
            from engine import device_inventory as di
            paths = pm.project_paths(project_id)
            inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
            if not any(d.get("enabled", True) and d.get("management_ip") for d in inventory.get("devices", [])):
                latest = None

        if not latest:
            card = self._card(self.content, fill="x")
            ttk.Label(card, text="아직 채점 이력이 없음 — '채점 실행' 메뉴에서 먼저 실행하세요",
                      style="Surface.TLabel", padding=16).pack()
            actions = ttk.Frame(self.content)
            actions.pack(anchor="w", pady=10)
            ttk.Button(actions, text="빠른 채점", style="Accent.TButton", command=self.quick_grade).pack(side="left")
            ttk.Button(actions, text="대시보드 초기화", command=self.reset_dashboard).pack(side="left", padx=8)
            return

        stages = latest["stages"]
        total_pass = sum(s["pass"] for s in stages)
        total_all = sum(s["total"] for s in stages)
        total_fail = total_all - total_pass
        pct = round(100 * total_pass / total_all) if total_all else 0

        metrics_row = tk.Frame(self.content, bg=COLOR["page"])
        metrics_row.pack(fill="x", pady=(0, 14))
        self._metric_card(metrics_row, "전체 진행률", f"{pct}%")
        self._metric_card(metrics_row, "PASS", total_pass, color=COLOR["success"])
        self._metric_card(metrics_row, "FAIL", total_fail, color=COLOR["danger"])
        self._metric_card(metrics_row, "소요시간", f"{latest['elapsed_sec']}초")

        ttk.Label(self.content, text="단계별 진행률", style="Heading.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 6))
        prog_card = self._card(self.content, fill="both", expand=True)
        prog_pad = ttk.Frame(prog_card, style="Surface.TFrame", padding=14)
        prog_pad.pack(fill="both", expand=True)

        for s in stages:
            row = ttk.Frame(prog_pad, style="Surface.TFrame")
            row.pack(fill="x", pady=4)
            top = ttk.Frame(row, style="Surface.TFrame")
            top.pack(fill="x")
            ttk.Label(top, text=s["label"], style="Surface.TLabel").pack(side="left")

            if s["status"] in ("SKIPPED", "NOT_STARTED"):
                ttk.Label(top, text="대기", foreground=COLOR["text_muted"], background=COLOR["surface"]).pack(side="right")
                pb = ttk.Progressbar(row, style="Neutral.Horizontal.TProgressbar", value=0, maximum=100)
            else:
                ratio = round(100 * s["pass"] / s["total"]) if s["total"] else 0
                color = COLOR["success"] if ratio == 100 else COLOR["danger"]
                ttk.Label(top, text=f"{s['pass']}/{s['total']}", foreground=color, background=COLOR["surface"]).pack(side="right")
                pb_style = "Success.Horizontal.TProgressbar" if ratio == 100 else "Danger.Horizontal.TProgressbar"
                pb = ttk.Progressbar(row, style=pb_style, value=ratio, maximum=100)
            pb.pack(fill="x", pady=(3, 0))
        actions = ttk.Frame(self.content)
        actions.pack(anchor="w", pady=10)
        ttk.Button(actions, text="빠른 채점", style="Accent.TButton", command=self.quick_grade).pack(side="left")
        ttk.Button(actions, text="대시보드 초기화", command=self.reset_dashboard).pack(side="left", padx=8)

    def quick_grade(self):
        self.show_grade()
        self.root.after(100, self.run_grade)

    def reset_dashboard(self):
        project_id = pm.get_active_project()
        if not project_id:
            return
        files = __import__("glob").glob(os.path.join("history", project_id, "*.json"))
        if files and not messagebox.askyesno("대시보드 초기화", "현재 프로젝트의 채점 이력을 모두 삭제하시겠습니까?"):
            return
        for path in files:
            if os.path.isfile(path):
                os.remove(path)
        self.show_dashboard()

    # ---------- Discovery ----------
    def show_discovery(self):
        self._set_active_nav("discovery")
        self._clear_content()
        ttk.Label(self.content, text="Discovery", style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.content, text=".unl 파일을 분석해 토폴로지·이미지버전·계층을 자동 확인",
                  foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 10))

        ttk.Button(self.content, text=".unl 파일 선택", style="Accent.TButton", command=self.run_discovery).pack(anchor="w", pady=(0, 10))

        card = self._card(self.content, fill="both", expand=True)
        self.discovery_text = scrolledtext.ScrolledText(card, font=("Consolas", 9), bg=COLOR["surface"],
                                                          fg=COLOR["text"], relief="flat", borderwidth=0)
        self.discovery_text.pack(fill="both", expand=True, padx=10, pady=10)

    def run_discovery(self):
        path = filedialog.askopenfilename(filetypes=[("EVE-NG lab", "*.unl"), ("모든 파일", "*.*")])
        if not path:
            return
        from unl_parser import run_discovery
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_discovery(path)
        self.discovery_text.delete("1.0", tk.END)
        self.discovery_text.insert(tk.END, buf.getvalue())

    # ---------- 커맨드 카탈로그 ----------
    def show_catalog(self):
        self._set_active_nav("catalog")
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text="커맨드 카탈로그", style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.content, text=project_id or "", foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 10))

        paths = pm.project_paths(project_id)
        self.catalog = cc.load_catalog(paths["commands_catalog"])
        self.catalog_path = paths["commands_catalog"]
        self.catalog_vars = {}
        self.catalog_fields = {}

        list_frame = tk.Frame(self.content, bg=COLOR["page"])
        list_frame.pack(fill="both", expand=True)

        badge = {"essential": ("필수", COLOR["accent"], COLOR["accent_bg"]),
                 "optional": ("선택사항", COLOR["warning"], COLOR["warning_bg"]),
                 "custom": ("커스텀", COLOR["success"], COLOR["success_bg"])}

        for category_key in ["essential", "optional", "custom"]:
            items = [c for c in self.catalog if c["category"] == category_key]
            if category_key == "custom" and not items:
                continue
            label_text, fg, bg = badge[category_key]
            head = tk.Label(list_frame, text=f"  {label_text}  ", bg=bg, fg=fg, font=("Segoe UI", 9, "bold"))
            head.pack(anchor="w", pady=(10, 4))

            card = self._card(list_frame, fill="x", pady=2)
            card_pad = ttk.Frame(card, style="Surface.TFrame", padding=(8, 4))
            card_pad.pack(fill="x")
            for item in items:
                row = ttk.Frame(card_pad, style="Surface.TFrame")
                row.pack(fill="x", pady=2)
                var = tk.BooleanVar(value=item["enabled"])
                self.catalog_vars[item["id"]] = var
                ttk.Checkbutton(row, variable=var).pack(side="left")
                command_var = tk.StringVar(value=item["command"])
                description_var = tk.StringVar(value=item["description"])
                ttk.Entry(row, textvariable=command_var, width=42).pack(side="left")
                ttk.Entry(row, textvariable=description_var).pack(side="left", fill="x", expand=True, padx=4)
                self.catalog_fields[item["id"]] = (command_var, description_var)
                ttk.Button(row, text="삭제", width=6,
                           command=lambda iid=item["id"]: self.remove_catalog_item(iid)).pack(side="left", padx=4)

        add_card = self._card(self.content, fill="x", pady=(10, 0))
        add_pad = ttk.Frame(add_card, style="Surface.TFrame", padding=10)
        add_pad.pack(fill="x")
        self.new_cmd_var = tk.StringVar()
        self.new_desc_var = tk.StringVar()
        ttk.Entry(add_pad, textvariable=self.new_cmd_var, width=30).pack(side="left", padx=(0, 4))
        ttk.Entry(add_pad, textvariable=self.new_desc_var, width=20).pack(side="left", padx=(0, 4))
        ttk.Button(add_pad, text="+ 추가", command=self.add_catalog_item).pack(side="left", padx=4)
        ttk.Button(add_pad, text="기본값 초기화", command=self.reset_catalog).pack(side="left", padx=4)
        ttk.Button(add_pad, text="저장", style="Accent.TButton", command=self.save_catalog_changes).pack(side="right")

    def add_catalog_item(self):
        cmd = self.new_cmd_var.get().strip()
        desc = self.new_desc_var.get().strip()
        if not cmd:
            messagebox.showwarning("입력 필요", "커맨드를 입력하세요")
            return
        cc.add_command(self.catalog, cmd, desc)
        cc.save_catalog(self.catalog, self.catalog_path)
        self.show_catalog()

    def remove_catalog_item(self, item_id):
        cc.remove_command(self.catalog, item_id)
        cc.save_catalog(self.catalog, self.catalog_path)
        self.show_catalog()

    def save_catalog_changes(self):
        for item in self.catalog:
            if item["id"] in self.catalog_vars:
                item["enabled"] = self.catalog_vars[item["id"]].get()
            if item["id"] in self.catalog_fields:
                command_var, description_var = self.catalog_fields[item["id"]]
                item["command"] = command_var.get().strip()
                item["description"] = description_var.get().strip()
        cc.save_catalog(self.catalog, self.catalog_path)
        messagebox.showinfo("저장됨", f"{self.catalog_path} 에 저장됨")

    def reset_catalog(self):
        if not messagebox.askyesno("카탈로그 초기화", "현재 카탈로그를 기본 커맨드로 되돌리시겠습니까?"):
            return
        catalog = cc.load_catalog("config/commands_catalog.yaml")
        cc.save_catalog(catalog, self.catalog_path)
        self.show_catalog()

    # ---------- 채점 실행 ----------
    def show_grade(self):
        self._set_active_nav("grade")
        self._clear_content()
        ttk.Label(self.content, text="채점 실행", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        opt_row = ttk.Frame(self.content, style="TFrame")
        opt_row.pack(anchor="w", pady=(0, 10))
        self.mock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_row, text="mock 모드(장비 접속 없이 테스트 데이터로 검증)", variable=self.mock_var).pack(side="left")
        ttk.Label(opt_row, text="실제 채점은 장비·SSH 설정이 필요합니다.", foreground=COLOR["warning"]).pack(side="left", padx=12)
        ttk.Button(opt_row, text="▶ 실행", style="Accent.TButton", command=self.run_grade).pack(side="left", padx=10)

        card = self._card(self.content, fill="both", expand=True)
        self.grade_text = scrolledtext.ScrolledText(card, font=("Consolas", 9), bg=COLOR["surface"],
                                                      fg=COLOR["text"], relief="flat", borderwidth=0)
        self.grade_text.pack(fill="both", expand=True, padx=10, pady=10)

    def run_grade(self):
        import main as main_module
        main_module.init_project(pm.get_active_project())

        collect_fn = main_module.mock_collect if self.mock_var.get() else main_module.real_collect
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                if self.mock_var.get():
                    print("[주의] mock 모드: 실제 장비 데이터가 아닌 내장 테스트 데이터로 채점합니다.")
                main_module.grade(collect_fn)
            except Exception as e:
                print(f"[오류] {e}")
        self.grade_text.delete("1.0", tk.END)
        self.grade_text.insert(tk.END, buf.getvalue())

    # ---------- 수집(Collection) ----------
    def show_collection(self):
        self._set_active_nav("collection")
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text="수집(Collection)", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        import main as main_module
        main_module.init_project(project_id)
        _, stages_cfg = main_module.load_lab_config()
        grading_cmds = main_module.get_all_commands(stages_cfg)

        paths = pm.project_paths(project_id)
        catalog = cc.load_catalog(paths["commands_catalog"])
        catalog_cmds = cc.get_enabled_commands(catalog)

        all_cmds = list(grading_cmds)
        for c in catalog_cmds:
            if c not in all_cmds:
                all_cmds.append(c)

        card = self._card(self.content, fill="x")
        pad = ttk.Frame(card, style="Surface.TFrame", padding=12)
        pad.pack(fill="x")
        ttk.Label(pad, text=f"이번 수집 시 실행될 커맨드 ({len(all_cmds)}개, 채점용+카탈로그 활성화분 합계)",
                  style="Surface.TLabel", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        for c in all_cmds:
            tk.Label(pad, text=f"  · {c}", bg=COLOR["surface"], fg=COLOR["text_secondary"],
                     font=("Consolas", 9), anchor="w").pack(anchor="w")

        ttk.Button(self.content, text="▶ 수집만 실행(채점 없이)", style="Accent.TButton",
                   command=self.run_collection_only).pack(anchor="w", pady=10)

        result_card = self._card(self.content, fill="both", expand=True)
        self.collection_text = scrolledtext.ScrolledText(result_card, font=("Consolas", 9), bg=COLOR["surface"],
                                                           fg=COLOR["text"], relief="flat", borderwidth=0)
        self.collection_text.pack(fill="both", expand=True, padx=10, pady=10)

    def run_collection_only(self):
        import main as main_module
        main_module.init_project(pm.get_active_project())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                result = main_module.real_collect()
                print(f"\n수집 완료 여부: {'성공' if result else '실패/중단'}")
                if result:
                    print(f"수집된 장비: {list(result.keys())}")
            except Exception as e:
                print(f"[오류] {e}")
        self.collection_text.delete("1.0", tk.END)
        self.collection_text.insert(tk.END, buf.getvalue())

    # ---------- 이력(History) ----------
    def show_history(self):
        self._set_active_nav("history")
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text="이력", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        import glob, json
        files = sorted(glob.glob(f"history/{project_id}/*.json"))
        if not files:
            card = self._card(self.content, fill="x")
            ttk.Label(card, text="이력 없음 — 채점을 먼저 실행하세요", style="Surface.TLabel", padding=16).pack()
            return

        card = self._card(self.content, fill="both", expand=True)
        tree = ttk.Treeview(card, columns=("elapsed",), show="tree headings", height=8)
        tree.heading("#0", text="세션")
        tree.heading("elapsed", text="소요시간(초)")
        for fp in files:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            tree.insert("", "end", iid=fp, text=data["session"], values=(data["elapsed_sec"],))
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        actions = ttk.Frame(self.content)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="최근 2개 회차 비교(diff)", style="Accent.TButton",
                   command=lambda: self.show_history_diff(files)).pack(side="left")
        ttk.Button(actions, text="선택 이력 보기", command=lambda: self.view_history(tree)).pack(side="left", padx=8)
        ttk.Button(actions, text="선택 이력 삭제", command=lambda: self.delete_history(tree)).pack(side="left", padx=8)
        ttk.Button(actions, text="전체 이력 삭제", command=self.delete_all_history).pack(side="left")

    def delete_history(self, tree):
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("선택 필요", "삭제할 이력을 선택하세요.")
            return
        if not messagebox.askyesno("삭제 확인", f"선택한 {len(selected)}개 이력을 삭제하시겠습니까?"):
            return
        for path in selected:
            if os.path.isfile(path):
                os.remove(path)
        self.show_history()

    def view_history(self, tree):
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("선택 필요", "볼 이력을 선택하세요.")
            return
        path = selected[0]
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        window = tk.Toplevel(self.root)
        window.title(f"채점 이력 — {data['session']}")
        window.geometry("900x650")
        output = scrolledtext.ScrolledText(window, font=("Consolas", 9))
        output.pack(fill="both", expand=True, padx=10, pady=10)
        output.insert(tk.END, json.dumps(data, ensure_ascii=False, indent=2))

    def delete_all_history(self):
        project_id = pm.get_active_project()
        import glob
        files = glob.glob(os.path.join("history", project_id, "*.json"))
        if not files:
            return
        if not messagebox.askyesno("전체 삭제 확인", f"'{project_id}'의 이력 {len(files)}개를 모두 삭제하시겠습니까?"):
            return
        for path in files:
            if os.path.isfile(path):
                os.remove(path)
        self.show_history()

    def show_history_diff(self, files):
        if len(files) < 2:
            messagebox.showinfo("안내", "비교하려면 이력이 2개 이상 필요함")
            return
        import json
        from engine.history import compare_sessions, compare_check_level
        with open(files[-2], encoding="utf-8") as f:
            prev = json.load(f)
        with open(files[-1], encoding="utf-8") as f:
            curr = json.load(f)

        win = tk.Toplevel(self.root)
        win.title(f"이력 비교 — {prev['session']} vs {curr['session']}")
        win.configure(bg=COLOR["page"])
        text = scrolledtext.ScrolledText(win, width=80, height=25, font=("Consolas", 9))
        text.pack(fill="both", expand=True, padx=10, pady=10)

        text.insert(tk.END, f"[Stage별 변화]\n")
        for d in compare_sessions(prev, curr):
            text.insert(tk.END, f"  {d['stage']}: {d['prev_pass']}/{d['prev_total']} -> {d['curr_pass']}/{d['curr_total']}  ({d['trend']})\n")
        text.insert(tk.END, f"\n[개별 체크 변화]\n")
        changes = compare_check_level(prev, curr)
        if not changes:
            text.insert(tk.END, "  변화 없음\n")
        for c in changes:
            text.insert(tk.END, f"  [{c['stage']}] {c['check']}: {c['from']} -> {c['to']}\n")

    # ---------- 보고서 ----------
    def show_report(self):
        self._set_active_nav("report")
        self._clear_content()
        ttk.Label(self.content, text="보고서", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Button(self.content, text="최신 채점 결과로 보고서 생성", style="Accent.TButton",
                   command=self.generate_report).pack(anchor="w", pady=(0, 10))

        card = self._card(self.content, fill="both", expand=True)
        self.report_text = scrolledtext.ScrolledText(card, font=("Consolas", 9), bg=COLOR["surface"],
                                                       fg=COLOR["text"], relief="flat", borderwidth=0)
        self.report_text.pack(fill="both", expand=True, padx=10, pady=10)

    def generate_report(self):
        project_id = pm.get_active_project()
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            messagebox.showwarning("안내", "채점 이력이 없음 — 먼저 채점을 실행하세요")
            return

        from ai_analysis.router import analyze as ai_analyze
        from report.markdown_report import build_markdown_report, save_markdown_report
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        md = build_markdown_report(project_id, latest["stages"], ai_result)

        paths = pm.project_paths(project_id)
        out_path = paths["target_state"].replace("target_state.yaml", "report_latest.md")
        save_markdown_report(project_id, latest["stages"], ai_result, out_path)

        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, md)
        messagebox.showinfo("완료", f"저장됨: {out_path}")

    # ---------- 연결 설정 ----------
    def show_connection_settings(self):
        self._set_active_nav("connection")
        self._clear_content()
        ttk.Label(self.content, text="연결 설정", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        import yaml
        conn_path = "connection.yaml"
        conn_cfg = {}
        if os.path.exists(conn_path):
            with open(conn_path, encoding="utf-8") as f:
                conn_cfg = yaml.safe_load(f) or {}

        net = conn_cfg.get("network", {})
        ssh = conn_cfg.get("ssh", {})

        card = self._card(self.content, fill="x")
        pad = ttk.Frame(card, style="Surface.TFrame", padding=14)
        pad.pack(fill="x")

        fields = [
            ("사전점검(pre-flight) 사용", "pre_flight_check", str(net.get("pre_flight_check", True))),
            ("사전점검 대상 장비", "check_target_node", net.get("check_target_node", "Core1")),
            ("사전점검 포트", "check_port", str(net.get("check_port", 22))),
            ("SSH 접속 타임아웃(초)", "ssh_timeout", str(ssh.get("connect_timeout_sec", 20))),
            ("재시도 횟수", "retry_count", str(ssh.get("retry_count", 1))),
            ("재시도 대기(초)", "retry_delay_sec", str(ssh.get("retry_delay_sec", 5))),
        ]
        self.conn_vars = {}
        for label, key, default in fields:
            row = ttk.Frame(pad, style="Surface.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, style="Surface.TLabel", width=22).pack(side="left")
            var = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=var, width=20).pack(side="left")
            self.conn_vars[key] = var

        ttk.Button(self.content, text="저장", style="Accent.TButton", command=self.save_connection_settings).pack(anchor="w", pady=10)

    def save_connection_settings(self):
        import yaml
        v = self.conn_vars
        conn_cfg = {
            "network": {
                "mode": "internal",
                "pre_flight_check": v["pre_flight_check"].get().strip().lower() == "true",
                "check_target_node": v["check_target_node"].get().strip(),
                "check_port": int(v["check_port"].get()),
                "check_timeout_sec": 3,
            },
            "ssh": {
                "connect_timeout_sec": int(v["ssh_timeout"].get()),
                "retry_count": int(v["retry_count"].get()),
                "retry_delay_sec": int(v["retry_delay_sec"].get()),
            },
        }
        with open("connection.yaml", "w", encoding="utf-8") as f:
            yaml.dump(conn_cfg, f, allow_unicode=True, sort_keys=False)
        messagebox.showinfo("저장됨", "connection.yaml에 저장됨")

    def show_inventory(self):
        self._set_active_nav("inventory")
        self._clear_content()
        ttk.Label(self.content, text="장비·SSH 설정", style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.content, text="각 장비의 관리 IP와 SSH 계정을 입력한 뒤 저장하세요.", foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 10))

        import yaml
        from engine import device_inventory as di
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        defaults = inventory.get("defaults", {})

        card = self._card(self.content, fill="both", expand=True)
        scroll = ScrollableFrame(card, COLOR["surface"])
        scroll.pack(fill="both", expand=True, padx=1, pady=1)
        pad = ttk.Frame(scroll.body, style="Surface.TFrame", padding=10)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="장비", style="Surface.TLabel", width=14).grid(row=0, column=0, sticky="w")
        ttk.Label(pad, text="관리 IP", style="Surface.TLabel", width=18).grid(row=0, column=1, sticky="w")
        ttk.Label(pad, text="포트", style="Surface.TLabel", width=8).grid(row=0, column=2, sticky="w")
        ttk.Label(pad, text="사용자", style="Surface.TLabel", width=14).grid(row=0, column=3, sticky="w")
        ttk.Label(pad, text="비밀번호", style="Surface.TLabel", width=14).grid(row=0, column=4, sticky="w")
        ttk.Label(pad, text="사용", style="Surface.TLabel", width=6).grid(row=0, column=5, sticky="w")
        ttk.Label(pad, text="비번 표시", style="Surface.TLabel", width=10).grid(row=0, column=6, sticky="w")
        ttk.Label(pad, text="삭제", style="Surface.TLabel", width=6).grid(row=0, column=7, sticky="w")
        self.inventory_vars = []
        for row, device in enumerate(inventory.get("devices", []), 1):
            values = {}
            for column, key, width in [(0, "name", 14), (1, "management_ip", 18), (2, "ssh_port", 8), (3, "username", 14), (4, "password", 14)]:
                default = defaults.get("default_ssh_port", 22) if key == "ssh_port" else "admin" if key in ("username", "password") else ""
                var = tk.StringVar(value=str(device.get(key) or default))
                entry = ttk.Entry(pad, textvariable=var, width=width, show="*" if key == "password" else "")
                entry.grid(row=row, column=column, sticky="ew", padx=2, pady=3)
                values[key] = var
            enabled = tk.BooleanVar(value=device.get("enabled", True))
            ttk.Checkbutton(pad, variable=enabled).grid(row=row, column=5, padx=5)
            visible = tk.BooleanVar(value=False)
            password_entry = pad.grid_slaves(row=row, column=4)[0]
            ttk.Checkbutton(pad, variable=visible, command=lambda entry=password_entry, var=visible: entry.configure(show="" if var.get() else "*")).grid(row=row, column=6, padx=5)
            ttk.Button(pad, text="삭제", command=lambda name=device["name"]: self.remove_inventory_device(name)).grid(row=row, column=7, padx=4)
            values["enabled"] = enabled
            self.inventory_vars.append((device["name"], values))
        add_row = ttk.Frame(pad, style="Surface.TFrame")
        add_row.grid(row=len(inventory.get("devices", [])) + 1, column=0, columnspan=8, sticky="w", pady=(10, 0))
        ttk.Button(add_row, text="+ 장비 행 추가", command=self.add_inventory_device).pack(side="left")
        ttk.Button(add_row, text="기본값 초기화", command=self.reset_inventory_defaults).pack(side="left", padx=6)
        ttk.Button(self.content, text="저장", style="Accent.TButton", command=self.save_inventory).pack(anchor="w", pady=10)

    def add_inventory_device(self):
        from engine import device_inventory as di
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        index = len(inventory.get("devices", [])) + 1
        di.add_device(inventory, {"name": f"Device{index}", "username": "admin", "password": "admin", "enabled": True})
        di.save_inventory(inventory, paths["device_inventory"])
        self.show_inventory()

    def remove_inventory_device(self, name):
        if not messagebox.askyesno("장비 삭제", f"'{name}' 장비를 삭제하시겠습니까?"):
            return
        from engine import device_inventory as di
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        di.remove_device(inventory, name)
        di.save_inventory(inventory, paths["device_inventory"])
        self.show_inventory()

    def reset_inventory_defaults(self):
        for _, values in self.inventory_vars:
            values["username"].set("admin")
            values["password"].set("admin")
            values["ssh_port"].set("22")

    def save_inventory(self):
        from engine import device_inventory as di
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        for name, values in self.inventory_vars:
            new_name = values["name"].get().strip()
            if not new_name:
                raise ValueError("장비 이름은 비워둘 수 없습니다.")
            di.update_device(inventory, name, {
                "name": new_name,
                "management_ip": values["management_ip"].get().strip(),
                "ssh_port": int(values["ssh_port"].get() or 22),
                "username": values["username"].get().strip(),
                "password": values["password"].get(),
                "enabled": values["enabled"].get(),
            })
        di.save_inventory(inventory, paths["device_inventory"])
        messagebox.showinfo("저장됨", "장비·SSH 설정이 저장되었습니다.")

    def show_terminal(self):
        self._set_active_nav("terminal")
        self._clear_content()
        ttk.Label(self.content, text="SSH 터미널", style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.content, text="여러 장비에 병렬 연결하고 분할 화면 또는 탭으로 확인합니다.", foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 10))
        from engine import device_inventory as di
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        devices = [d for d in inventory.get("devices", []) if d.get("management_ip") and d.get("enabled", True)]
        pad = ttk.Frame(self._card(self.content, fill="x"), style="Surface.TFrame", padding=10)
        pad.pack(fill="x")
        self.terminal_devices = {d["name"]: d for d in devices}
        self.terminal_select_vars = {name: tk.BooleanVar(value=True) for name in self.terminal_devices}
        ttk.Label(pad, text="병렬 연결 수", style="Surface.TLabel").pack(side="left")
        self.terminal_count_var = tk.IntVar(value=min(4, len(devices)) or 1)
        ttk.Spinbox(pad, from_=1, to=max(1, len(devices)), textvariable=self.terminal_count_var, width=5).pack(side="left", padx=6)
        ttk.Button(pad, text="전체 선택", command=lambda: self.set_terminal_selection(True)).pack(side="left", padx=3)
        ttk.Button(pad, text="전체 해제", command=lambda: self.set_terminal_selection(False)).pack(side="left", padx=3)
        ttk.Button(pad, text="병렬 연결", style="Accent.TButton", command=self.connect_terminals).pack(side="left", padx=6)
        ttk.Button(pad, text="분할 보기", command=lambda: self.set_terminal_view("split")).pack(side="left", padx=3)
        ttk.Button(pad, text="탭 보기", command=lambda: self.set_terminal_view("tabs")).pack(side="left", padx=3)
        selection = ttk.Frame(self._card(self.content, fill="x"), style="Surface.TFrame", padding=8)
        selection.pack(fill="x", pady=(8, 0))
        for name, var in self.terminal_select_vars.items():
            ttk.Checkbutton(selection, text=name, variable=var).pack(side="left", padx=4)
        self.terminal_notebook = ttk.Notebook(self.content)
        self.terminal_notebook.pack(fill="both", expand=True, pady=8)
        self.terminal_outputs = {}
        self.terminal_connections = {}
        if devices:
            self._add_terminal_pane(devices[0]["name"])
            self.terminal_outputs[devices[0]["name"]].insert(tk.END, "연결할 장비를 선택하고 병렬 연결을 누르세요.\n")
        command_row = ttk.Frame(self.content)
        command_row.pack(fill="x", pady=(8, 0))
        self.terminal_command_var = tk.StringVar()
        entry = ttk.Entry(command_row, textvariable=self.terminal_command_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.send_terminal_command())
        ttk.Button(command_row, text="전체 실행", command=self.send_terminal_command).pack(side="left", padx=6)

    def set_terminal_selection(self, selected):
        for var in self.terminal_select_vars.values():
            var.set(selected)

    def connect_terminals(self):
        selected = [self.terminal_devices[name] for name, var in self.terminal_select_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning("장비 없음", "관리 IP가 입력된 장비가 없습니다.")
            return
        limit = max(1, int(self.terminal_count_var.get()))
        for device in selected[:limit]:
            self._add_terminal_pane(device["name"])
            threading.Thread(target=self._terminal_connect_worker, args=(device,), daemon=True).start()

    def _add_terminal_pane(self, name):
        if name in self.terminal_outputs:
            return
        frame = ttk.Frame(self.terminal_notebook)
        output = scrolledtext.ScrolledText(frame, font=("Consolas", 9), bg="#101418", fg="#D6F5D6", insertbackground="white", relief="flat")
        output.pack(fill="both", expand=True, padx=4, pady=4)
        self.terminal_notebook.add(frame, text=name)
        self.terminal_outputs[name] = output

    def _terminal_connect_worker(self, device):
        try:
            from engine import device_inventory as di
            try:
                from netmiko import ConnectHandler
            except ModuleNotFoundError:
                raise RuntimeError("netmiko가 설치되지 않았습니다. PowerShell에서 'py.exe -m pip install netmiko'를 실행하세요.")
            project_id = pm.get_active_project()
            paths = pm.project_paths(project_id)
            inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
            ip, port, username, password = di.resolve_credentials(device, inventory["defaults"])
            model = str(device.get("model") or "").lower()
            device_type = "linux" if any(value in model for value in ("linux", "ubuntu", "debian", "centos")) else "arista_eos"
            conn = ConnectHandler(device_type=device_type, host=ip, port=port, username=username, password=password, timeout=20)
            self.root.after(0, self._terminal_connected, conn, device["name"])
        except Exception as error:
            detail = str(error)
            self.root.after(0, lambda name=device["name"], message=detail: self.terminal_outputs[name].insert(tk.END, f"연결 실패: {message}\n"))

    def _terminal_connected(self, connection, name):
        self.terminal_connections[name] = connection
        self.terminal_outputs[name].insert(tk.END, f"[{name}] SSH 연결됨\n")

    def set_terminal_view(self, mode):
        if mode == "tabs":
            self.terminal_notebook.pack(fill="both", expand=True, pady=8)
            return
        panes = list(self.terminal_outputs.items())
        self.terminal_notebook.pack_forget()
        split = ttk.Frame(self.content)
        split.pack(fill="both", expand=True, pady=8)
        for name, output in panes:
            frame = ttk.Frame(split)
            frame.pack(side="left", fill="both", expand=True)
            ttk.Label(frame, text=name).pack(anchor="w")
            output.pack_forget()
            output.pack(in_=frame, fill="both", expand=True)

    def send_terminal_command(self):
        command = self.terminal_command_var.get().strip()
        if not command:
            return
        self.terminal_command_var.set("")
        for name, connection in self.terminal_connections.items():
            threading.Thread(target=self._terminal_command_worker, args=(connection, name, command), daemon=True).start()

    def _terminal_command_worker(self, connection, name, command):
        try:
            output = connection.send_command(command)
            self.root.after(0, lambda: self.terminal_outputs[name].insert(tk.END, f"\n$ {command}\n{output}\n"))
        except Exception as error:
            detail = str(error)
            self.root.after(0, lambda device_name=name, message=detail: self.terminal_outputs[device_name].insert(tk.END, f"\n[오류] {message}\n"))

    # ---------- AI 설정 ----------
    def show_ai_settings(self):
        self._set_active_nav("ai")
        self._clear_content()
        ttk.Label(self.content, text="AI 설정", style="Heading.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self.content, text="설정이 없거나 전부 실패해도 규칙기반 분석은 항상 동작함",
                  foreground=COLOR["text_secondary"]).pack(anchor="w", pady=(0, 10))

        card = self._card(self.content, fill="x")
        pad = ttk.Frame(card, style="Surface.TFrame", padding=14)
        pad.pack(fill="x")
        import yaml
        config_path = "ai_config.yaml"
        config = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
        local = next((p for p in config.get("providers", []) if p.get("type") == "local"), {})
        self.ai_endpoint_var = tk.StringVar(value=local.get("endpoint", "http://localhost:13305"))
        configured_order = [
            provider.get("type")
            for provider in config.get("providers", [])
            if provider.get("type") in {"rule_based", "local", "api", "gemini"}
        ]
        type_map = {"rule_based": "program", "local": "local", "api": "cloud", "gemini": "cloud"}
        self.ai_order = []
        for provider_type in configured_order:
            provider = type_map[provider_type]
            if provider not in self.ai_order:
                self.ai_order.append(provider)
        self.ai_order.extend(provider for provider in ("program", "local", "cloud") if provider not in self.ai_order)
        ttk.Label(pad, text="분석 모델 순서", style="Surface.TLabel").pack(anchor="w")
        ttk.Label(pad, text="상자를 마우스로 드래그해 우선순위를 변경하세요.", style="Muted.TLabel").pack(anchor="w", pady=(2, 4))
        self.ai_order_canvas = tk.Canvas(
            pad,
            height=112,
            bg=COLOR["surface"],
            highlightthickness=1,
            highlightbackground=COLOR["border"],
        )
        self.ai_order_canvas.pack(fill="x", pady=(0, 8))
        self.ai_order_canvas.bind("<Button-1>", self.start_ai_drag)
        self.ai_order_canvas.bind("<B1-Motion>", self.move_ai_drag)
        self.ai_order_canvas.bind("<ButtonRelease-1>", self.finish_ai_drag)
        self.ai_order_canvas.bind("<Configure>", lambda event: self.render_ai_order())
        self.render_ai_order()
        ttk.Label(pad, text="우선순위", style="Surface.TLabel").pack(anchor="w")
        ttk.Label(pad, text="1. 규칙기반 → 2. 로컬 AI(Lemonade) → 3. 승인 후 클라우드 AI", style="Surface.TLabel").pack(anchor="w", pady=(2, 10))
        row = ttk.Frame(pad, style="Surface.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Lemonade 주소", style="Surface.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.ai_endpoint_var, width=34).pack(side="left", padx=8)
        ttk.Button(row, text="연결 확인", command=self.check_local_ai).pack(side="left")
        ttk.Button(row, text="저장", style="Accent.TButton", command=self.save_ai_settings).pack(side="left", padx=6)
        self.ai_status = ttk.Label(pad, text="로컬 AI 미확인", style="Muted.TLabel")
        self.ai_status.pack(anchor="w", pady=(8, 0))
        security = self._card(self.content, fill="x", pady=10)
        security_pad = ttk.Frame(security, style="Surface.TFrame", padding=14)
        security_pad.pack(fill="x")
        ttk.Label(security_pad, text="클라우드 AI 보안 게이트", style="Surface.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(security_pad, text="고객사 IP·호스트명·MAC 등은 변환 후 로컬 매핑표로 보관하고, 변환된 자료만 전송합니다.", style="Muted.TLabel").pack(anchor="w", pady=4)
        ttk.Button(security_pad, text="승인 후 클라우드 AI 분석", command=self.run_cloud_ai).pack(anchor="w", pady=(4, 0))

    def render_ai_order(self):
        labels = {"program": ("프로그램", "규칙기반"), "local": ("로컬 AI", "Lemonade"), "cloud": ("클라우드 AI", "승인 필요")}
        self.ai_order_canvas.delete("all")
        self.ai_order_boxes = {}
        width = max(self.ai_order_canvas.winfo_width(), 450)
        column_width = width / 3
        self.ai_order_canvas.create_rectangle(5, 5, width - 5, 107, outline=COLOR["border"], fill=COLOR["surface"])
        for index, provider in enumerate(self.ai_order):
            x1 = column_width * index + 12
            x2 = column_width * (index + 1) - 12
            tags = ("ai_box", provider)
            box = self.ai_order_canvas.create_rectangle(x1, 16, x2, 96, fill=COLOR["accent_bg"], outline=COLOR["accent"], width=2, tags=tags)
            title, detail = labels[provider]
            self.ai_order_canvas.create_text((x1 + x2) / 2, 40, text=f"{index + 1}. {title}", fill=COLOR["text"], font=("Segoe UI", 10, "bold"), tags=tags)
            self.ai_order_canvas.create_text((x1 + x2) / 2, 68, text=detail, fill=COLOR["text_secondary"], tags=tags)
            self.ai_order_boxes[box] = provider
        self.ai_order_canvas.tag_raise("ai_box")

    def start_ai_drag(self, event):
        self.ai_drag_provider = None
        self.ai_drag_start_x = event.x
        self.ai_drag_moved = False
        for item, provider in self.ai_order_boxes.items():
            x1, y1, x2, y2 = self.ai_order_canvas.coords(item)
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.ai_drag_provider = provider
                self.ai_drag_box = item
                self.ai_drag_canvas_items = self.ai_order_canvas.find_withtag(provider)
                self.ai_order_canvas.itemconfigure(item, fill=COLOR["accent"], outline=COLOR["accent"])
                break

    def move_ai_drag(self, event):
        if not getattr(self, "ai_drag_provider", None):
            return
        if abs(event.x - self.ai_drag_start_x) < 20:
            return
        self.ai_drag_moved = True
        self.ai_order_canvas.configure(cursor="hand2")
        box = self.ai_drag_box
        x1, y1, x2, y2 = self.ai_order_canvas.coords(box)
        delta = event.x - (x1 + x2) / 2
        self.ai_order_canvas.move(self.ai_drag_provider, delta, 0)

    def finish_ai_drag(self, event):
        provider = getattr(self, "ai_drag_provider", None)
        if not provider:
            return
        self.ai_order_canvas.configure(cursor="")
        if getattr(self, "ai_drag_moved", False):
            width = max(self.ai_order_canvas.winfo_width(), 450)
            target = max(0, min(2, int(event.x / (width / 3))))
            current = self.ai_order.index(provider)
            if target != current:
                self.ai_order.remove(provider)
                self.ai_order.insert(target, provider)
                self.save_ai_settings()
        self.render_ai_order()
        self.ai_drag_provider = None

    def save_ai_settings(self):
        import yaml
        provider_map = {
            "program": {"type": "rule_based"},
            "local": {"type": "local", "endpoint": self.ai_endpoint_var.get().strip()},
            "cloud": {"type": "api", "api_key_env": "ANTHROPIC_API_KEY"},
        }
        with open("ai_config.yaml", "w", encoding="utf-8") as file:
            yaml.dump({"providers": [provider_map[item] for item in self.ai_order]}, file, allow_unicode=True, sort_keys=False)
        self.ai_status.configure(text="AI 설정 저장됨")

    def check_local_ai(self):
        endpoint = self.ai_endpoint_var.get().rstrip("/")
        try:
            with urllib.request.urlopen(endpoint, timeout=3):
                self.ai_status.configure(text="로컬 AI 연결됨")
        except Exception as error:
            self.ai_status.configure(text=f"로컬 AI 연결 실패: {error}")

    def run_cloud_ai(self):
        if not messagebox.askyesno("클라우드 AI 전송 승인", "보안자료를 변환한 결과만 클라우드 AI로 전송합니다. 진행하시겠습니까?"):
            return
        from engine.history import load_latest
        latest = load_latest(pm.get_active_project())
        if not latest:
            messagebox.showwarning("이력 없음", "분석할 채점 이력이 없습니다.")
            return
        from ai_analysis.router import analyze
        result = analyze(latest["stages"], ai_config={"providers": [{"type": "api", "api_key_env": "ANTHROPIC_API_KEY"}]}, user_approved_cloud=True)
        messagebox.showinfo("클라우드 AI 분석", result.get("summary", "분석 결과 없음"))


if __name__ == "__main__":
    root = tk.Tk()
    app = LabGraderApp(root)
    root.mainloop()
