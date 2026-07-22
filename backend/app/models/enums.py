"""도메인 열거형 (PRD 5장).

DB 컬럼은 PRD DDL 대로 VARCHAR 로 저장하고(native enum 미사용, 마이그레이션 유연성),
값의 집합만 Python `StrEnum` 으로 강제한다. 서비스 계층에서 검증/직렬화에 사용한다.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """시스템 전역 역할 (users.role). 그룹 역할과는 별개 축 (PRD 3.6.1).

    SUPER_ADMIN 은 셋업 위저드로 만들어지는 최초 계정에만 부여되며(마이그레이션/CLI 외에는
    API 로 부여 불가), 다른 사용자에게 admin 권한을 부여/회수할 수 있다. ADMIN 은 그 외 모든
    관리 기능을 수행하되 역할(권한) 부여/회수는 하지 못한다.
    """

    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


# admin 권한을 가진 역할 집합 — require_admin/셋업 존재 판정 등 "관리자인가?" 검사에 쓴다.
ADMIN_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN})


class UserStatus(StrEnum):
    """계정 상태 (users.status). 가입 코드제이므로 코드 검증 통과 시 즉시 ACTIVE 이며,
    admin 이 비활성화하면 INACTIVE 가 된다 (PRD 3.1 — 2026-07-19 승인제에서 전환).

    구 승인제의 pending/rejected 는 폐지됐고, 마이그레이션 0003 에서 inactive 로 이관된다.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"


class GroupRole(StrEnum):
    """그룹 내 역할 (group_members.role) (PRD 3.1.2)."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class SharePermission(StrEnum):
    """공유 링크 권한 (shares.permission) (PRD 3.4)."""

    READ = "read"
    DOWNLOAD = "download"
    WRITE = "write"


class GroupPermission(StrEnum):
    """파일/폴더에 대한 그룹 권한 (file_group_permissions.permission) (PRD 3.1.2)."""

    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


class TodoStatus(StrEnum):
    """데일리 투두 항목 상태 (todo_items.status).

    - PENDING: 미완료(빈칸, 기본). DONE: 완료(체크). FAILED: 수행 실패(X — 스스로 못 했다고 표시).

    FAILED 는 '오늘은 안 함(skip)'이 아니라 자기 반성으로 남기는 명시적 실패다. 따라서 달성률
    분모에서 빼지 않고 미달성으로 집계하며, 실패가 하나라도 있는 날은 '달성'이 아니다.
    """

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class RoutineFrequency(StrEnum):
    """반복 루틴 주기 (routines.frequency).

    - DAILY: 매일. WEEKLY: 특정 요일(days_of_week 에 월=0..일=6 저장).
    """

    DAILY = "daily"
    WEEKLY = "weekly"
