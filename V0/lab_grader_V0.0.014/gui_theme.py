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


def apply_theme_colors(mode):
    if mode == "dark":
        COLOR.update({"page": "#202124", "surface": "#292A2D", "surface_alt": "#303134", "border": "#4A4B4F", "text": "#F1F3F4", "text_secondary": "#BDC1C6", "text_muted": "#9AA0A6"})
        return
    COLOR.update({"page": "#F7F7F5", "surface": "#FFFFFF", "surface_alt": "#F1F1EF", "border": "#E0E0DC", "text": "#1A1A18", "text_secondary": "#5F5E5A", "text_muted": "#9A998F"})
