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

    # 로깅 (PRD 11장). log_format=json(운영)/console(개발 컬러). log_level 은 stdlib 레벨명.
    log_format: str = "console"
    log_level: str = "INFO"

    # 메트릭 (PRD 11장). /metrics 노출 여부 — 게이트웨이에서 별도 차단(nginx deny).
    metrics_enabled: bool = True

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

    # 첫 admin 은 셋업 위저드(POST /api/setup)로 생성한다 (PRD 3.6.2).
    # ADMIN_* 환경변수 시드는 제거됨 — 운영 중 비상 복구는 CLI `python -m app.cli create-admin`.

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

    # 재개 가능 업로드 (PRD 3.2). 파트 크기는 S3 최소(5MiB) 이상이어야 한다(마지막 파트 제외).
    # 세션 TTL 초과분은 고아 multipart 로 간주해 abort + 세션 정리한다.
    resumable_part_size: int = 8 * 1024 * 1024        # 8 MiB
    resumable_session_ttl_seconds: int = 24 * 60 * 60  # 24h

    # RAG 인덱싱 (PRD 3.7, Phase 7-1). arq 워커가 업로드/버전/삭제 훅으로 큐잉된 잡을 처리한다.
    #   indexing_enabled=False 로 전체 비활성화(훅이 큐잉을 건너뛴다).
    indexing_enabled: bool = True
    # 임베딩 프로바이더: upstage(solar-embedding-1-large, 4096d) | fake(결정적 해시, 외부 호출 없음).
    # 키가 없는데 upstage 면 인덱싱 잡이 경고 후 스킵된다(fail-open — 파일 서비스 본연 기능 불영향).
    embedding_provider: str = "upstage"
    upstage_api_key: str | None = None
    # 문서 파싱/임베딩 모델명(런타임에 실제 키가 있을 때만 사용).
    upstage_embedding_model: str = "solar-embedding-1-large"
    # 인덱싱 대상 최대 원본 크기(초과 시 스킵) — backend 로 전체 적재하므로 상한을 둔다.
    indexing_max_file_size: int = 20 * 1024 * 1024  # 20 MiB
    # 청킹 파라미터 (RecursiveCharacterTextSplitter, 문자 기준).
    indexing_chunk_size: int = 1000
    indexing_chunk_overlap: int = 150


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
