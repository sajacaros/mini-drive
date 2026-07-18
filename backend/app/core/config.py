from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 설정. 환경변수 또는 .env 에서 로드한다."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 일반
    app_name: str = "Mini Drive"
    environment: str = "development"
    debug: bool = False

    # PostgreSQL (asyncpg)
    database_url: str = "postgresql+asyncpg://postgres:password@db:5432/minidrive"

    # MinIO / S3 호환 오브젝트 스토리지
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "change-me-in-production"
    minio_bucket: str = "minidrive"
    minio_secure: bool = False

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # 첫 admin 부트스트랩 (PRD 3.6.2)
    admin_email: str = "admin@example.com"
    admin_initial_password: str = "change-me-in-production"

    # CORS: 프론트엔드 오리진 (게이트웨이 경유 시 동일 오리진이므로 로컬 개발용)
    cors_origins: list[str] = ["http://localhost", "http://localhost:5173"]

    # Rate limiting (PRD 10장). 분당 허용 횟수 — 테스트/운영에서 환경변수로 조정 가능.
    # rate_limit_enabled=False 로 전체 비활성화(테스트 편의).
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_login_per_min: int = 5       # 로그인: IP 당
    rate_limit_register_per_min: int = 3    # 회원가입: IP 당
    rate_limit_refresh_per_min: int = 10    # 토큰 갱신: IP 당
    rate_limit_upload_per_min: int = 10     # 업로드: user 당
    rate_limit_lookup_per_min: int = 20     # 이메일 조회: user 당


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
