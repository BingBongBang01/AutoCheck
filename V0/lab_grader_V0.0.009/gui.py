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
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, scrolledtext

from engine import project_manager as pm
from engine import command_catalog as cc

# ---------- 색상 팔레트 (머티리얼 톤) ----------
COLOR = {
    "page": "#F7F7F5",
    "surface": "#FFFFFF",
    "surface_alt": "#F1F1EF",
    "border": "#E0E0DC",
    "text": "#1A1A18",
    "text_secondary": "#5F5E5A",
    "text_muted": "#9A998F",
    "accent": "#185FA5",
    "accent_bg": "#E6F1FB",
    "success": "#3B6D11",
    "success_bg": "#EAF3DE",
    "danger": "#A32D2D",
    "danger_bg": "#FCEBEB",
    "warning": "#854F0B",
    "warning_bg": "#FAEEDA",
}


class LabGraderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LAB 자동채점 프로그램")
        self.root.geometry("980x640")
        self.root.configure(bg=COLOR["page"])

        self._setup_styles()

        self.active_project = pm.get_active_project()
        self.nav_buttons = {}
        self.current_nav = "dashboard"

        self._build_top_bar()
        self._build_body()
        self.refresh_projects()
        self.show_dashboard()

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

        style.configure("Success.Horizontal.TProgressbar", background=COLOR["success"], troughcolor=COLOR["surface_alt"])
        style.configure("Danger.Horizontal.TProgressbar", background=COLOR["danger"], troughcolor=COLOR["surface_alt"])
        style.configure("Neutral.Horizontal.TProgressbar", background=COLOR["text_muted"], troughcolor=COLOR["surface_alt"])

        style.configure("Treeview", background=COLOR["surface"], fieldbackground=COLOR["surface"],
                         foreground=COLOR["text"], rowheight=26, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=COLOR["surface_alt"], foreground=COLOR["text_secondary"],
                         font=("Segoe UI", 9, "bold"))

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
            ("ai", "AI 설정", self.show_ai_settings),
        ]
        for key, label, cmd in nav_items:
            btn = ttk.Button(nav_inner, text=label, style="Nav.TButton", command=cmd)
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        outer_content = tk.Frame(body, bg=COLOR["page"])
        outer_content.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(outer_content, padding=16)
        self.content.pack(fill="both", expand=True)

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

        if not latest:
            card = self._card(self.content, fill="x")
            ttk.Label(card, text="아직 채점 이력이 없음 — '채점 실행' 메뉴에서 먼저 실행하세요",
                      style="Surface.TLabel", padding=16).pack()
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

        scroll_outer = tk.Frame(self.content, bg=COLOR["page"])
        scroll_outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_outer, bg=COLOR["page"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_outer, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas, bg=COLOR["page"])
        list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

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
                tk.Label(row, text=item["command"], width=42, anchor="w", font=("Consolas", 9),
                         bg=COLOR["surface"], fg=COLOR["text"]).pack(side="left")
                tk.Label(row, text=item["description"], anchor="w", font=("Segoe UI", 9),
                         bg=COLOR["surface"], fg=COLOR["text_secondary"]).pack(side="left", fill="x", expand=True)
                if category_key == "custom":
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
        cc.save_catalog(self.catalog, self.catalog_path)
        messagebox.showinfo("저장됨", f"{self.catalog_path} 에 저장됨")

    # ---------- 채점 실행 ----------
    def show_grade(self):
        self._set_active_nav("grade")
        self._clear_content()
        ttk.Label(self.content, text="채점 실행", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        opt_row = ttk.Frame(self.content, style="TFrame")
        opt_row.pack(anchor="w", pady=(0, 10))
        self.mock_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_row, text="mock 모드(장비 접속 없이 파이프라인만 검증)", variable=self.mock_var).pack(side="left")
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

        ttk.Button(self.content, text="최근 2개 회차 비교(diff)", style="Accent.TButton",
                   command=lambda: self.show_history_diff(files)).pack(anchor="w", pady=8)

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

        rows = [
            ("1순위", "API (Anthropic)", "환경변수 ANTHROPIC_API_KEY 필요"),
            ("2순위", "로컬 NPU (Gemma/Lemonade)", "http://localhost:13305"),
            ("최종", "규칙기반(rule_based)", "항상 사용 가능 — 네트워크/키 불필요"),
        ]
        for tag, name, detail in rows:
            row = ttk.Frame(pad, style="Surface.TFrame")
            row.pack(fill="x", pady=4)
            tk.Label(row, text=tag, bg=COLOR["accent_bg"], fg=COLOR["accent"], font=("Segoe UI", 8, "bold"),
                     padx=6, pady=1).pack(side="left")
            ttk.Label(row, text=f"  {name}", style="Surface.TLabel", width=28).pack(side="left")
            ttk.Label(row, text=detail, foreground=COLOR["text_secondary"], background=COLOR["surface"]).pack(side="left")

        ttk.Label(self.content, text="(현재 버전은 규칙기반이 기본 — API/로컬 연동 활성화는 다음 버전에서 UI로 지원 예정)",
                  foreground=COLOR["text_muted"]).pack(anchor="w", pady=(10, 0))


if __name__ == "__main__":
    root = tk.Tk()
    app = LabGraderApp(root)
    root.mainloop()
