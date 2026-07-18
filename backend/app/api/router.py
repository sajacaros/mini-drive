from fastapi import APIRouter

from app.api.routes import admin, auth, files

api_router = APIRouter(prefix="/api")

# 이후 스테이지에서 아래 지점에 도메인 라우터를 include 한다.
# from app.api.routes import auth, files, shares, groups, permissions, admin
#
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
# api_router.include_router(shares.router,      prefix="/shares", tags=["shares"])
# api_router.include_router(groups.router,      prefix="/groups", tags=["groups"])
# api_router.include_router(permissions.router, prefix="/permissions", tags=["permissions"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
