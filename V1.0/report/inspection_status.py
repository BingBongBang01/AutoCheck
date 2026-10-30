"""점검 항목 판정 상태 문자열 — 렌더러(엑셀/PDF)와 빌더가 공유하는 어휘.

왜 별도 모듈인가: 이 값들은 원래 report/inspection_excel.py 안에 있었는데, 그 모듈은
최상단에서 openpyxl 을 import 한다(스타일 상수를 모듈 레벨에서 만들기 때문에 미룰 수 없다).
그래서 "정상"이라는 문자열 하나를 쓰려는 쪽까지 openpyxl 전체(이전 측정(Windows): 269 ms)를
끌어오게 됐고, 앱을 켜는 경로가 그 체인에 걸려 있었다:

    api/inspection_report_api.py -> engine/inspection_report_builder.py
                                 -> report/inspection_excel.py -> openpyxl

판정 어휘는 엑셀과 아무 관계가 없으므로 여기로 내렸다. inspection_excel 은 하위 호환을 위해
이 값들을 그대로 재노출한다(기존 import 경로가 깨지지 않는다).
"""

__all__ = ["STATUS_OK", "STATUS_WARN", "STATUS_NA", "STATUS_SKIP", "STATUS_UNREACHABLE",
           "ALL_STATUSES", "NOT_JUDGED_STATUSES"]

STATUS_OK = "정상"
STATUS_WARN = "확인필요"
STATUS_NA = "미수집"
# '해당없음' — 장비에 그 기능/하드웨어가 아예 없어서 점검 대상이 아닌 경우.
# 왜 '미수집'과 나누는가: 둘 다 값이 없지만 원인과 조치가 정반대다.
#   미수집   = 봐야 하는데 못 봤다(명령이 거부됨/센서가 응답 없음)  -> 다음 회차에 다시 시도
#   해당없음 = 볼 것이 없다(BGP/EVPN 미구성, 가상 장비라 PSU 없음) -> 조치 불필요
# 예전에는 둘 다 '확인필요'로 떨어져서 vEOS-lab 같은 가상 장비가 매 회차 오탐을 냈다.
STATUS_SKIP = "해당없음"
STATUS_UNREACHABLE = "접속 불가"

# 보고서에 나타날 수 있는 전체 상태 — 판정 로직이 이 집합 밖의 값을 내면 렌더러가 조용히
# 기본 서식으로 떨어지므로, 새 상태를 추가할 때 여기에도 넣어야 한다.
ALL_STATUSES = (STATUS_OK, STATUS_WARN, STATUS_NA, STATUS_SKIP, STATUS_UNREACHABLE)

# '정상도 비정상도 아닌' 상태 — 요약표에서 정상 칸에도 비정상 칸에도 넣으면 안 되는 것들.
# 이 집합이 없으면 미수집 항목이 조용히 '정상'으로 합산돼 점검 커버리지가 부풀려진다.
NOT_JUDGED_STATUSES = frozenset({STATUS_NA, STATUS_SKIP, STATUS_UNREACHABLE})
