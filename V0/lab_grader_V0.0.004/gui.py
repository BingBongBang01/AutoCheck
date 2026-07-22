"""
LAB 자동채점 프로그램 — 데스크톱 UI (Tkinter, 표준 라이브러리만 사용 = 추가 설치 불필요)

실행: python gui.py

주의: 이 샌드박스 환경(디스플레이 없음)에서는 실제 창을 띄워 테스트할 수 없음.
      본인 노트북(Windows, 디스플레이 있음)에서 실행해야 창이 뜸.
      로직 부분(프로젝트/카탈로그 조작)은 별도로 이미 단위테스트 완료된 함수를 그대로 재사용함.
"""
import io
import os
import sys
import contextlib
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, scrolledtext

from engine import project_manager as pm
from engine import command_catalog as cc


class LabGraderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LAB 자동채점 프로그램")
        self.root.geometry("900x600")

        self.active_project = pm.get_active_project()
        self._build_top_bar()
        self._build_body()
        self.refresh_projects()
        self.show_dashboard()

    # ---------- 상단 바 (프로젝트 선택) ----------
    def _build_top_bar(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="프로젝트:").pack(side="left", padx=(0, 6))

        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(bar, textvariable=self.project_var, state="readonly", width=30)
        self.project_combo.pack(side="left")
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_selected)

        ttk.Button(bar, text="새 프로젝트", command=self.on_new_project).pack(side="left", padx=4)
        ttk.Button(bar, text="이름 변경", command=self.on_rename_project).pack(side="left", padx=4)
        ttk.Button(bar, text="삭제", command=self.on_delete_project).pack(side="left", padx=4)

    # ---------- 본문 (좌측 네비 + 우측 컨텐츠) ----------
    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        nav = ttk.Frame(body, padding=8, width=140)
        nav.pack(side="left", fill="y")

        for label, cmd in [
            ("대시보드", self.show_dashboard),
            ("Discovery", self.show_discovery),
            ("커맨드 카탈로그", self.show_catalog),
            ("채점 실행", self.show_grade),
        ]:
            ttk.Button(nav, text=label, command=cmd).pack(fill="x", pady=2)

        self.content = ttk.Frame(body, padding=10)
        self.content.pack(side="left", fill="both", expand=True)

    def _clear_content(self):
        for widget in self.content.winfo_children():
            widget.destroy()

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
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text=f"대시보드 — {project_id}", font=("", 12, "bold")).pack(anchor="w", pady=(0, 10))

        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None

        if not latest:
            ttk.Label(self.content, text="아직 채점 이력이 없음 — '채점 실행' 메뉴에서 먼저 실행하세요").pack(anchor="w")
            return

        ttk.Label(self.content, text=f"최근 회차: {latest['session']}  (소요 {latest['elapsed_sec']}초)").pack(anchor="w", pady=(0, 8))

        tree = ttk.Treeview(self.content, columns=("status", "pass", "total"), show="tree headings", height=10)
        tree.heading("#0", text="단계")
        tree.heading("status", text="상태")
        tree.heading("pass", text="pass")
        tree.heading("total", text="total")
        tree.column("#0", width=200)
        for stage in latest["stages"]:
            tree.insert("", "end", text=stage["label"], values=(stage["status"], stage["pass"], stage["total"]))
        tree.pack(fill="both", expand=True)

    # ---------- Discovery ----------
    def show_discovery(self):
        self._clear_content()
        ttk.Label(self.content, text="Discovery — .unl 파일 분석", font=("", 12, "bold")).pack(anchor="w", pady=(0, 10))

        btn_frame = ttk.Frame(self.content)
        btn_frame.pack(anchor="w", pady=(0, 8))
        ttk.Button(btn_frame, text=".unl 파일 선택", command=self.run_discovery).pack(side="left")

        self.discovery_text = scrolledtext.ScrolledText(self.content, height=25, font=("Consolas", 9))
        self.discovery_text.pack(fill="both", expand=True)

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
        self._clear_content()
        project_id = pm.get_active_project()
        ttk.Label(self.content, text=f"커맨드 카탈로그 — {project_id}", font=("", 12, "bold")).pack(anchor="w", pady=(0, 10))

        paths = pm.project_paths(project_id)
        self.catalog = cc.load_catalog(paths["commands_catalog"])
        self.catalog_path = paths["commands_catalog"]
        self.catalog_vars = {}

        canvas_frame = ttk.Frame(self.content)
        canvas_frame.pack(fill="both", expand=True)

        list_frame = ttk.Frame(canvas_frame)
        list_frame.pack(fill="both", expand=True)

        for category_label, category_key in [("필수", "essential"), ("선택사항", "optional"), ("커스텀", "custom")]:
            items = [c for c in self.catalog if c["category"] == category_key]
            if category_key == "custom" and not items:
                continue
            ttk.Label(list_frame, text=category_label, font=("", 10, "bold")).pack(anchor="w", pady=(8, 2))
            for item in items:
                row = ttk.Frame(list_frame)
                row.pack(fill="x", pady=1)
                var = tk.BooleanVar(value=item["enabled"])
                self.catalog_vars[item["id"]] = var
                ttk.Checkbutton(row, variable=var).pack(side="left")
                ttk.Label(row, text=item["command"], width=45, font=("Consolas", 9)).pack(side="left")
                ttk.Label(row, text=item["description"], foreground="#666").pack(side="left")
                if category_key == "custom":
                    ttk.Button(row, text="삭제", width=6,
                               command=lambda iid=item["id"]: self.remove_catalog_item(iid)).pack(side="left", padx=4)

        add_frame = ttk.Frame(self.content, padding=(0, 10, 0, 0))
        add_frame.pack(fill="x")
        self.new_cmd_var = tk.StringVar()
        self.new_desc_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.new_cmd_var, width=30).pack(side="left", padx=(0, 4))
        ttk.Entry(add_frame, textvariable=self.new_desc_var, width=20).pack(side="left", padx=(0, 4))
        ttk.Button(add_frame, text="추가", command=self.add_catalog_item).pack(side="left", padx=4)
        ttk.Button(add_frame, text="저장", command=self.save_catalog_changes).pack(side="right")

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
        self._clear_content()
        ttk.Label(self.content, text="채점 실행", font=("", 12, "bold")).pack(anchor="w", pady=(0, 10))

        self.mock_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.content, text="mock 모드(장비 접속 없이 파이프라인만 검증)", variable=self.mock_var).pack(anchor="w")
        ttk.Button(self.content, text="실행", command=self.run_grade).pack(anchor="w", pady=8)

        self.grade_text = scrolledtext.ScrolledText(self.content, height=25, font=("Consolas", 9))
        self.grade_text.pack(fill="both", expand=True)

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


if __name__ == "__main__":
    root = tk.Tk()
    app = LabGraderApp(root)
    root.mainloop()
