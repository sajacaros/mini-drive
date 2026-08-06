"""ORM 모델 패키지.

모든 모델을 여기서 export 하여 Alembic autogenerate 가 `Base.metadata` 를 통해
전체 테이블을 인식하도록 한다. 서비스 계층은 `from app.models import User, File` 형태로 사용한다.
"""

from app.core.database import Base
from app.models.app_setting import AppSetting
from app.models.audit import AuditLog
from app.models.board import (
    Board,
    BoardAttachment,
    BoardComment,
    BoardGroup,
    BoardPost,
)
from app.models.chat import ChatMessage, ChatSession
from app.models.enums import (
    BoardPermission,
    ChatRole,
    GroupPermission,
    GroupRole,
    RoutineFrequency,
    SharePermission,
    TodoStatus,
    UserRole,
    UserStatus,
)
from app.models.favorite import FileFavorite, FileRecent
from app.models.file import File, FileVersion
from app.models.group import Group, GroupMember
from app.models.permission import FileGroupPermission
from app.models.share import Share
from app.models.signup_code import SignupCode
from app.models.todo import Routine, TodoItem
from app.models.upload_session import UploadSession
from app.models.user import User
from app.models.wiki import WikiDocument

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
    "FileFavorite",
    "FileRecent",
    "AuditLog",
    "UploadSession",
    "SignupCode",
    "AppSetting",
    "Routine",
    "TodoItem",
    "WikiDocument",
    "ChatSession",
    "ChatMessage",
    "Board",
    "BoardGroup",
    "BoardPost",
    "BoardComment",
    "BoardAttachment",
    # 열거형
    "UserRole",
    "UserStatus",
    "GroupRole",
    "SharePermission",
    "GroupPermission",
    "BoardPermission",
    "TodoStatus",
    "RoutineFrequency",
    "ChatRole",
]
