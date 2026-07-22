"""그룹 관리 라우터 (PRD 6.4).

그룹 목록/상세/멤버 목록은 조직 내 공개 조회(비멤버 허용, PRD 3.1.1). 생성/수정/삭제/멤버 관리는
그룹 역할(owner/admin/member) 가드를 서비스 계층에서 적용한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.models import GroupMember
from app.schemas.groups import (
    GroupCreateRequest,
    GroupDetailResponse,
    GroupListResponse,
    GroupMemberListResponse,
    GroupMemberResponse,
    GroupResponse,
    GroupSummaryResponse,
    GroupUpdateRequest,
    MemberInviteRequest,
    MemberRoleUpdateRequest,
    TransferOwnershipRequest,
)
from app.services import groups as groups_service
from app.services.groups import GroupServiceError

router = APIRouter()


def _http_error(exc: GroupServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _member_response(
    member: GroupMember, email: str, name: str
) -> GroupMemberResponse:
    return GroupMemberResponse(
        user_id=member.user_id,
        email=email,
        display_name=name,
        role=member.role,
        joined_at=member.joined_at,
    )


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreateRequest, user: CurrentUser, session: DbSession
) -> GroupResponse:
    """그룹 생성 (PRD 6.4). 생성자가 owner 로 자동 등록. 이름 중복 시 409."""
    try:
        group = await groups_service.create_group(
            session, user, payload.name, payload.description
        )
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return GroupResponse.model_validate(group)


@router.get("", response_model=GroupListResponse)
async def list_groups(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> GroupListResponse:
    """활성 그룹 목록 (멤버 수·내 역할 포함, 페이지네이션) — PRD 6.4."""
    rows, total = await groups_service.list_groups(session, user.id, page, size)
    return GroupListResponse(
        items=[
            GroupSummaryResponse(
                **GroupResponse.model_validate(g).model_dump(),
                member_count=count,
                my_role=my_role,
                owner_display_name=owner_name,
            )
            for g, count, my_role, owner_name in rows
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int, user: CurrentUser, session: DbSession
) -> GroupDetailResponse:
    """그룹 상세 — 정보 + 활성 멤버 목록. 비멤버도 조회 가능 (PRD 3.1.1)."""
    try:
        (
            group,
            member_count,
            my_role,
            owner_name,
            members,
        ) = await groups_service.get_group_detail(session, group_id, user.id)
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return GroupDetailResponse(
        **GroupResponse.model_validate(group).model_dump(),
        member_count=member_count,
        my_role=my_role,
        owner_display_name=owner_name,
        members=[_member_response(m, email, name) for m, email, name in members],
    )


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    payload: GroupUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> GroupResponse:
    """이름/설명 수정 (owner/admin 만). 이름 중복 시 409 (PRD 6.4)."""
    try:
        group = await groups_service.update_group(
            session,
            user,
            group_id,
            name=payload.name,
            description=payload.description,
            description_set="description" in payload.model_fields_set,
        )
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return GroupResponse.model_validate(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """그룹 소프트 삭제 (owner 만) — 파일 권한 일괄 제거 + 그룹 소유 파일 개인 전환 (PRD 6.4)."""
    try:
        await groups_service.delete_group(session, user, group_id)
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{group_id}/members", response_model=GroupMemberListResponse)
async def list_members(
    group_id: int, user: CurrentUser, session: DbSession
) -> GroupMemberListResponse:
    """그룹원 목록 (PRD 6.4). 비멤버도 조회 가능."""
    try:
        members = await groups_service.get_group_members(session, group_id)
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return GroupMemberListResponse(
        group_id=group_id,
        items=[_member_response(m, email, name) for m, email, name in members],
    )


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    group_id: int,
    payload: MemberInviteRequest,
    user: CurrentUser,
    session: DbSession,
) -> GroupMemberResponse:
    """그룹원 초대 (owner/admin 만). 재초대 upsert. owner 직접 부여 금지 (PRD 6.4)."""
    try:
        member, email, name = await groups_service.add_member(
            session, user, group_id, payload.user_id, payload.role
        )
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return _member_response(member, email, name)


@router.delete(
    "/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    group_id: int, user_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """그룹원 제거 (owner/admin 만). admin 은 admin/owner 제거 불가 (PRD 6.4)."""
    try:
        await groups_service.remove_member(session, user, group_id, user_id)
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{group_id}/members/{user_id}/role", response_model=GroupMemberResponse
)
async def change_member_role(
    group_id: int,
    user_id: int,
    payload: MemberRoleUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> GroupMemberResponse:
    """그룹원 역할 변경 (owner 만, admin|member) (PRD 6.4)."""
    try:
        member, email, name = await groups_service.change_member_role(
            session, user, group_id, user_id, payload.role
        )
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return _member_response(member, email, name)


@router.post("/{group_id}/transfer-ownership", response_model=GroupResponse)
async def transfer_ownership(
    group_id: int,
    payload: TransferOwnershipRequest,
    user: CurrentUser,
    session: DbSession,
) -> GroupResponse:
    """소유권 이전 (owner 만, 대상은 활성 멤버) — 역할 스왑 (PRD 6.4)."""
    try:
        group = await groups_service.transfer_ownership(
            session, user, group_id, payload.user_id
        )
    except GroupServiceError as exc:
        raise _http_error(exc) from exc
    return GroupResponse.model_validate(group)
