"""위키 라우터 (spec/wiki-index.md) — 인덱싱된 문서 목록.

파일 단위 토글(`/api/files/{id}/wiki`)은 경로가 `/api/files/*` 라 files 라우터에 둔다 —
권한 부여 엔드포인트와 같은 이유다(routes/permissions.py 주석 참조). 이 라우터는
`/api/wiki/*` 만 담당한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.wiki import WikiDocumentItem, WikiDocumentListResponse
from app.services import wiki as wiki_service

router = APIRouter()


@router.get("/documents", response_model=WikiDocumentListResponse)
async def list_documents(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> WikiDocumentListResponse:
    """내가 접근할 수 있는, 인덱싱된 문서 목록.

    권한 필터는 목록을 만드는 단계에 건다 — 만든 뒤 거르지 않는다. 접근할 수 없는 문서는
    개수에도 잡히지 않는다.
    """
    items, total = await wiki_service.list_documents(session, user, page, size)
    return WikiDocumentListResponse(
        items=[
            WikiDocumentItem(
                file_id=i.file_id,
                name=i.name,
                owner_display_name=i.owner_display_name,
                status=i.status,
                version=i.version,
                indexed_at=i.indexed_at.isoformat() if i.indexed_at else None,
                node_count=i.node_count,
            )
            for i in items
        ],
        total=total,
    )
