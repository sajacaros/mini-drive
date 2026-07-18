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
    """가입 승인제 상태 (users.status). 신규 가입은 PENDING 으로 시작 (PRD 3.1)."""

    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    REJECTED = "rejected"


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
