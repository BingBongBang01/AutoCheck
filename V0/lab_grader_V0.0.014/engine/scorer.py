"""
stage별 결과를 집계하고, depends_on 미충족 단계는 SKIPPED 처리한다.
"""


def stage_pass_rate(results):
    if not results:
        return None  # 체크가 아직 없는 단계(미착수)
    passed = sum(1 for r in results if r["result"] == "PASS")
    return passed, len(results)


def score_all(stage_order, stage_results_map):
    """
    stage_order: stages.yaml 파싱 결과 (id, label, depends_on 포함, 순서 보장)
    stage_results_map: {stage_id: [Verdict, ...]}
    반환: [{"id", "label", "status", "pass": n, "total": n, "results": [...]}]
    """
    scored = []
    completed_stage_ids = set()

    for stage in stage_order:
        deps = stage["depends_on"]
        deps_ok = all(d in completed_stage_ids for d in deps)

        results = stage_results_map.get(stage["id"], [])

        if not deps_ok:
            scored.append({
                "id": stage["id"], "label": stage["label"], "status": "SKIPPED",
                "pass": 0, "total": len(results), "results": results,
                "note": f"선행 단계 미완료로 채점 보류: {deps}",
            })
            continue

        rate = stage_pass_rate(results)
        if rate is None:
            scored.append({
                "id": stage["id"], "label": stage["label"], "status": "NOT_STARTED",
                "pass": 0, "total": 0, "results": [], "note": "체크 항목 미정의(미착수)",
            })
            continue

        passed, total = rate
        is_complete = (passed == total)
        if is_complete:
            completed_stage_ids.add(stage["id"])

        scored.append({
            "id": stage["id"], "label": stage["label"],
            "status": "COMPLETE" if is_complete else "IN_PROGRESS",
            "pass": passed, "total": total, "results": results, "note": None,
        })

    return scored


def print_scoreboard(scored, session_label=""):
    print("=" * 60)
    print(f"LAB1 Campus 자동 채점 결과  {session_label}")
    print("=" * 60)
    for s in scored:
        if s["status"] == "SKIPPED":
            print(f"\n[{s['label']}]  (SKIPPED — {s['note']})")
            continue
        if s["status"] == "NOT_STARTED":
            print(f"\n[{s['label']}]  (미착수)")
            continue

        bar_len = 20
        filled = int(bar_len * s["pass"] / s["total"]) if s["total"] else 0
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\n[{s['label']}]  {bar} {s['pass']}/{s['total']}")
        for r in s["results"]:
            mark = {"PASS": "v", "FAIL": "x", "UNKNOWN": "?"}.get(r["result"], "x")
            line = f"  {mark} {r['check']}"
            if r["result"] in ("FAIL", "UNKNOWN"):
                line += f"   (기대값: {r['expected']} / 실제: {r['actual']})"
            print(line)
    print("\n" + "=" * 60)
