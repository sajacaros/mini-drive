"""ORM 모델 패키지.

모든 모델을 여기서 export 하여 Alembic autogenerate 가 `Base.metadata` 를 통해
전체 테이블을 인식하도록 한다. 서비스 계층은 `from app.models import User, File` 형태로 사용한다.
"""

from app.core.database import Base
from app.models.app_setting import AppSetting
from app.models.audit import AuditLog
from app.models.enums import (
    GroupPermission,
    GroupRole,
    SharePermission,
    UserRole,
    UserStatus,
)
from app.models.file import File, FileVersion
from app.models.group import Group, GroupMember
from app.models.permission import FileGroupPermission
from app.models.share import Share
from app.models.signup_code import SignupCode
from app.models.upload_session import UploadSession
from app.models.user import User

__all__ = [
    "Base",
    # 테이블 모델
    "User",
    "File",
    "FileVersion",
    "Share",
    "Group",
    "GroupMember",
    "FileGroupPermission",
    "AuditLog",
    "UploadSession",
    "SignupCode",
    "AppSetting",
    # 열거형
    "UserRole",
    "UserStatus",
    "GroupRole",
    "SharePermission",
    "GroupPermission",
]
