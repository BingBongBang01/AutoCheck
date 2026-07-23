import tkinter as tk
from tkinter import ttk


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, background, padding=0):
        super().__init__(parent, padding=padding)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=background)
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._update_region)
        self.canvas.bind("<Configure>", self._resize_body)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _update_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_body(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _wheel(self, event):
        delta = -1 if event.num == 4 else 1 if event.num == 5 else -int(event.delta / 120)
        try:
            if self.canvas.winfo_exists():
                self.canvas.yview_scroll(delta, "units")
        except tk.TclError:
            return
