"""
정기점검 주기 실행 엔진.
외부 의존성(APScheduler 등) 추가 없이 5필드 cron 표현식(분 시 일 월 요일)만 직접 해석한다.
"정기점검 실행 -> history 저장 -> 이전 회차 diff 연산 -> 결과 리포트 생성"은
이미 grade_via_pipeline()의 Pipeline(HistoryStep/AlarmStep/ReportStep)이 처리하므로,
여기서는 "설정된 일정에 어떤 프로젝트를 실행할지"만 결정한다.

실행 방법:
  python -m engine.scheduler          # 지금 이 순간 due한 job만 1회 실행 (Windows 작업 스케줄러/cron이 매분 호출하는 걸 전제)
  python -m engine.scheduler --loop   # 별도 스케줄러 없이 자체 루프로 매분 체크 (테스트/단독 운영용)
"""
import os
import time
import datetime
import yaml

SCHEDULER_CONFIG = "config/scheduler.yaml"


def _match_field(field_str, value):
    if field_str == "*":
        return True
    return str(value) in [p.strip() for p in field_str.split(",")]


def cron_matches(expr, dt):
    """expr: "분 시 일 월 요일" (요일: 0=일요일 ~ 6=토요일, cron 관례)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron 표현식은 5필드(분 시 일 월 요일)여야 함: {expr!r}")
    minute, hour, day, month, weekday = fields
    dt_weekday = dt.isoweekday() % 7  # datetime.isoweekday(): 월=1..일=7 -> cron 관례(일=0)로 변환
    return (_match_field(minute, dt.minute) and _match_field(hour, dt.hour)
            and _match_field(day, dt.day) and _match_field(month, dt.month)
            and _match_field(weekday, dt_weekday))


def load_jobs(path=SCHEDULER_CONFIG):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("jobs", [])


def run_job(project_id):
    """engine.grading의 init_project + grade_via_pipeline을 그대로 재사용 —
    정기점검 실행 흐름(history 저장/diff/알람/리포트)을 새로 만들지 않고 그대로 위임."""
    from engine import grading
    from engine import log_storage
    grading.init_project(project_id)
    # 고객사/프로파일을 넘겨야 수집 원본이 data/<고객사>/<프로파일>/runs/<run_id>/raw/에 쌓인다.
    # 예전에는 인자 없이 호출해서 레거시 raw_logs/{lab}/로 떨어졌고, 그 폴더는 점검 로그·보고서
    # 탭이 보지 않으므로 스케줄러가 수집한 로그는 UI에서 영영 보이지 않았다.
    customer_name, profile_name = log_storage.resolve_names_from_project(project_id)
    return grading.grade_via_pipeline(
        lambda: grading.real_collect(customer_name, profile_name))


def run_due_jobs(now=None, jobs_path=SCHEDULER_CONFIG):
    """now 시각 기준으로 cron이 일치하는 enabled job들을 실행하고, 실행된 project_id 목록을 반환."""
    now = now or datetime.datetime.now()
    ran = []
    for job in load_jobs(jobs_path):
        if not job.get("enabled", True):
            continue
        cron = job.get("cron")
        project_id = job.get("project_id")
        if not cron or not project_id:
            print(f"[스케줄러] 잘못된 job 설정(건너뜀): {job}")
            continue
        if not cron_matches(cron, now):
            continue
        print(f"[스케줄러] {project_id} 실행 (cron={cron}, now={now.isoformat(timespec='minutes')})")
        try:
            run_job(project_id)
            ran.append(project_id)
        except Exception as e:
            print(f"[스케줄러] {project_id} 실행 실패: {e}")
    return ran


def run_loop(poll_sec=5, jobs_path=SCHEDULER_CONFIG):
    """OS 스케줄러(cron/작업 스케줄러) 없이 단독으로 매분 체크하는 자체 루프. 테스트/소규모 운영용."""
    print("[스케줄러] 루프 시작 (분 단위 체크, Ctrl+C로 종료)")
    last_minute = None
    while True:
        now = datetime.datetime.now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if minute_key != last_minute:
            run_due_jobs(now, jobs_path)
            last_minute = minute_key
        time.sleep(poll_sec)


if __name__ == "__main__":
    import sys
    if "--loop" in sys.argv:
        run_loop()
    else:
        ran = run_due_jobs()
        if not ran:
            print("[스케줄러] 지금 시각에 실행할 job 없음")
