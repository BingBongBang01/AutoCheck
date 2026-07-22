import html


def build_html_report(project, stages, output_path):
    passed = sum(stage.get("pass", 0) for stage in stages)
    total = sum(stage.get("total", 0) for stage in stages)
    body = []
    for stage in stages:
        body.append(f"<tr><td>{html.escape(stage.get('label', ''))}</td><td>{stage.get('pass', 0)}</td><td>{stage.get('total', 0)}</td><td>{html.escape(stage.get('status', ''))}</td></tr>")
    content = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>{html.escape(project)} 점검 보고서</title>
<style>body{{font-family:Arial;background:#1e1e1e;color:#ddd;padding:32px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #444;padding:8px;text-align:left}}th{{background:#252526}}</style>
<h1>{html.escape(project)} 점검 보고서</h1><p>통과: {passed} / {total}</p><table><tr><th>단계</th><th>PASS</th><th>전체</th><th>상태</th></tr>{''.join(body)}</table></html>"""
    with open(output_path, "w", encoding="utf-8") as stream:
        stream.write(content)
    return output_path
