"""권한 검사 라우터 (PRD 6.6) — 내 유효 권한 / 상속 권한 조회.

파일 그룹 권한 부여/수정/회수 및 shared-with-me 는 `/api/files/*` 경로이므로 files 라우터에
둔다(routes/files.py). 이 라우터는 `/api/permissions/*` 내부용 조회만 담당한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DbSession
from app.schemas.permissions import (
    InheritedPermissionResponse,
    InheritedPermissionsResponse,
    PermissionCheckResponse,
)
from app.services import permissions as permissions_service
from app.services.permissions import PermissionServiceError

router = APIRouter()


def _http_error(exc: PermissionServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/check/{file_id}", response_model=PermissionCheckResponse)
async def check_permission(
    file_id: int, user: CurrentUser, session: DbSession
) -> PermissionCheckResponse:
    """현재 사용자의 파일 유효 권한 (PRD 6.6). owner=manage, 그 외 그룹 권한 판정."""
    try:
        result = await permissions_service.check_permission(session, user, file_id)
    except PermissionServiceError as exc:
        raise _http_error(exc) from exc
    return PermissionCheckResponse(
        file_id=file_id,
        permission=result.permission,
        via=result.via,
        source_file_id=result.source_file_id,
    )


@router.get("/inherited/{file_id}", response_model=InheritedPermissionsResponse)
async def inherited_permissions(
    file_id: int, user: CurrentUser, session: DbSession
) -> InheritedPermissionsResponse:
    """파일의 상속된 유효 권한 트리 (PRD 6.6, 디버깅/관리용). manage 권한자만."""
    try:
        inherited = await permissions_service.list_inherited(session, user, file_id)
    except PermissionServiceError as exc:
        raise _http_error(exc) from exc
    return InheritedPermissionsResponse(
        file_id=file_id,
        inherited=[
            InheritedPermissionResponse(
                group_id=g.group_id,
                group_name=g.group_name,
                permission=g.permission,
                source_file_id=g.source_file_id,
                source_file_name=g.source_file_name,
                depth=g.depth,
                expires_at=g.expires_at,
            )
            for g in inherited
        ],
    )
