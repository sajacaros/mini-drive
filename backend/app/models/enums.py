"""도메인 열거형 (PRD 5장).

DB 컬럼은 PRD DDL 대로 VARCHAR 로 저장하고(native enum 미사용, 마이그레이션 유연성),
값의 집합만 Python `StrEnum` 으로 강제한다. 서비스 계층에서 검증/직렬화에 사용한다.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """시스템 전역 역할 (users.role). 그룹 역할과는 별개 축 (PRD 3.6.1)."""

    USER = "user"
    ADMIN = "admin"


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
