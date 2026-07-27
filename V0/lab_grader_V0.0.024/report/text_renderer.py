from core.health_score import deduction_for, score_project

def render_text_report(findings):
    """
    3섹션 텍스트 리포트를 생성한다.
    1. Total Score & Pass/Fail counts
    2. Deductions
    3. Passed Summary
    """
    total_findings = len(findings)
    pass_count = sum(1 for f in findings if f.result == "PASS")
    fail_count = sum(1 for f in findings if f.result == "FAIL")
    
    score_data = score_project(findings)
    total_score = score_data.get("project_score", 100)
    
    output = []
    output.append("="*50)
    output.append(f"Total Score: {total_score} pts")
    output.append(f"Total Checks: {total_findings} (PASS: {pass_count}, FAIL: {fail_count})")
    output.append("="*50)
    
    output.append("\n[Deductions]")
    deductions = [f for f in findings if f.result == "FAIL"]
    if not deductions:
        output.append("No deductions. Perfect score!")
    else:
        for f in deductions:
            points = deduction_for(f)
            output.append(f"- [{f.device} / {f.category}] {f.check_id}: FAIL (-{points} pts)")
            output.append(f"  ㄴ Expected: {f.expected} | Actual: {f.actual}")
            
    output.append("\n[Passed Summary]")
    passed = [f for f in findings if f.result == "PASS"]
    if not passed:
        output.append("No checks passed.")
    else:
        # Group by category
        passed_by_category = {}
        for f in passed:
            passed_by_category[f.category] = passed_by_category.get(f.category, 0) + 1
            
        for category, count in passed_by_category.items():
            output.append(f"- [{category}] {count} checks passed")
            
    output.append("="*50)
    return "\n".join(output)
