from fastapi import APIRouter

from app.api.routes import admin, auth, files, shares

api_router = APIRouter(prefix="/api")

# 이후 스테이지에서 아래 지점에 도메인 라우터를 include 한다.
# from app.api.routes import auth, files, shares, groups, permissions, admin
#
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(shares.router, prefix="/shares", tags=["shares"])
# 무인증 공개 공유 접근 — 인증 라우터와 경로 충돌을 피하려 별도 prefix 로 분리 (shares.py 참조).
api_router.include_router(
    shares.public_router, prefix="/public/shares", tags=["public-shares"]
)
# api_router.include_router(groups.router,      prefix="/groups", tags=["groups"])
# api_router.include_router(permissions.router, prefix="/permissions", tags=["permissions"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
