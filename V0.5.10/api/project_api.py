"""
ProjectApiMixin — 프로젝트(=고객사/점검 프로파일) 조합.

각 프로젝트는 labs/<id> 폴더 하나로 완전히 독립(장비목록/커맨드카탈로그/점검기준/이력 등 전부 별도) —
"고객사별 프로파일"과 "점검 프로파일" 요구사항을 하나의 프로젝트 개념으로 통합해서 충족한다.

세부 구현은 도메인별로 분리되어 있다:
  - ProjectCoreApiMixin (api/project_core_api.py): 프로젝트 원시 CRUD + zip 내보내기/불러오기
  - CustomerProfileApiMixin (api/customer_profile_api.py): 고객사/정기점검 프로파일 트리 관리
"""
from api.project_core_api import ProjectCoreApiMixin
from api.customer_profile_api import CustomerProfileApiMixin


class ProjectApiMixin(CustomerProfileApiMixin, ProjectCoreApiMixin):
    pass
