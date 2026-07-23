import difflib
import html
import os
from datetime import datetime


SNAPSHOT_COMMANDS = [
    "show version", "show inventory", "show hostname", "show clock", "show users",
    "show running-config", "show startup-config", "show vlan", "show interfaces status",
    "show spanning-tree", "show ip interface brief",
]


def save_snapshot(root, phase, device, outputs):
    path = os.path.join(root, "snapshots", phase, device)
    os.makedirs(path, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    for command, output in outputs.items():
        filename = command.replace(" ", "_").replace("/", "_")
        with open(os.path.join(path, f"{stamp}_{filename}.txt"), "w", encoding="utf-8") as stream:
            stream.write(output or "")


def diff_text(before, after):
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="before", tofile="after"))


def build_html_diff(before, after):
    rows = []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        marker = line[:2]
        color = {"+ ": "#d4edda", "- ": "#f8d7da", "  ": "transparent"}.get(marker, "#fff3cd")
        rows.append(f'<div style="background:{color}">{html.escape(line)}</div>')
    return "\n".join(rows)
