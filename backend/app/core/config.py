from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 개발 편의를 위한 placeholder. 운영에 이 값이 남아 있으면 기동을 거부한다
# (Settings._forbid_insecure_secrets).
PLACEHOLDER_SECRET = "change-me-in-production"

# HS256 서명키 최소 길이. RFC 7518 3.2 가 해시 출력 길이(32바이트) 이상을 요구한다.
MIN_JWT_SECRET_BYTES = 32


class InsecureSecretError(RuntimeError):
    """운영 환경에 개발용 기본 비밀키가 남아 있을 때 기동을 막는다."""


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

    # 배치 업로드 (폴더 업로드). 작은 파일을 한 요청에 묶어 요청 수를 줄이기 위한 상한이며,
    # 파일 1개 크기 상한(MAX_FILE_SIZE=10GB)과는 무관하다 — max_batch_bytes 는 요청 본문 **합계**다.
    # 이 값을 넘는 파일은 클라이언트가 배치에 담지 않고 기존 단일/재개 업로드 경로로 보낸다.
    max_batch_files: int = 200
    max_batch_bytes: int = 64 * 1024 * 1024           # 64 MiB

    # 휴지통 보존 기간 — 자동 영구 삭제 (spec/trash-retention-purge.md).
    # 기본값 7 = 설정을 주지 않아도 자동 정리가 켜진다. 끄려면 명시적으로 0 을 준다.
    # **업그레이드 주의**: 기존 배포는 deleted_at 이 오래 전부터 쌓여 있어, 이 값을 그대로 받으면
    # 첫 회차가 기한 초과분을 한꺼번에 지운다. 올리기 전에 규모를 확인한다(아무것도 지우지 않는다):
    #     python -m app.cli purge-trash --dry-run
    # purge_hour 는 KST 기준 실행 시각 — 되돌릴 수 없는 삭제라 사람이 안 쓰는 시간에 돈다.
    # purge_batch 는 한 트랜잭션의 크기 제한이며 하루 처리량 제한이 아니다(한 회차 안에서
    # 배치가 상한보다 적게 돌아올 때까지 반복한다).
    trash_retention_days: int = Field(default=7, ge=0)
    trash_purge_hour: int = Field(default=4, ge=0, le=23)
    trash_purge_batch: int = Field(default=200, ge=1)
    # 회차당 개별 SSE 이벤트 상한. 초과하면 소유자별 요약 1건으로 접는다(이벤트 폭주 방지).
    trash_purge_event_cap: int = Field(default=200, ge=0)

    # --- 위키 인덱싱 (spec/wiki-index.md) ---
    wiki_enabled: bool = True
    # 사내 vLLM. LiteLLM 경유이므로 hosted_vllm/ 프리픽스 + base_url 조합으로 붙는다.
    # 기본값을 비워 둔다 — 사내 주소를 리포에 박지 않기 위해서다. WIKI_LLM_BASE_URL 로 주입한다.
    wiki_llm_base_url: str = ""
    wiki_llm_api_key: str = ""
    wiki_llm_model: str = "hosted_vllm/solar-open2-250b"
    # 생성 계열 호출의 추론 예산. Solar-Open2 는 reasoning 모델이라 기본값이면 호출당 수천
    # 토큰/수십 초를 태운다. low 로 내려도 요약 품질과 긴 JSON 완결성이 유지된다(실측).
    wiki_llm_reasoning_effort: str = "low"
    # 인덱싱 대상 크기 상한. 트리는 본문을 담지 않아 원문과 비슷한 크기다.
    wiki_max_input_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    # 위키를 끈 뒤 트리를 실제로 지우기까지의 유예(일). 0 이면 즉시 삭제.
    wiki_purge_grace_days: int = Field(default=30, ge=0)
    # 노드 선택 프롬프트에 넣을 축약 트리의 문자 예산 — 모델 컨텍스트(131,072 토큰)에서
    # 역산한 하드 캡이다. 이게 없으면 문서가 늘었을 때 모든 질의가 400 으로 죽는다
    # (실측: 467건을 전부 넣으면 932,753자로 컨텍스트의 7배였다).
    #
    # 값은 실측 토큰 비율로 정했다 — 한국어+마크다운 혼합에서 **자/토큰 2.47~2.60**이라
    # 180,000자 = 약 66,000 토큰이고 컨텍스트의 절반이다. 답변 생성은 별도 호출이라 이 예산을
    # 나눠 쓰지 않는다. 비율이 2.0 으로 나빠져도 90,000 토큰이라 여유가 남는다.
    # 넉넉히 잡는 이유는 활용성이다 — 60,000자에서는 467건 중 103건만 훑었고 180,000자는
    # 454건을 훑는다. 예산은 사고를 막는 상한이지 검색 범위를 좁히는 수단이 아니다.
    wiki_query_catalog_budget_chars: int = Field(default=180_000, ge=1_000)

    # --- 그룹 게시판 (spec/group-board.md) ---
    # 첨부는 드라이브와 분리되어 개인 할당량을 차감하지 않는다. 대신 작게 묶는다 — 큰 파일은
    # 드라이브에 올리고 공유 링크를 글에 붙이는 것이 이 기능의 전제다.
    # 10 MB 상한이라 단일 요청 multipart 로 충분하고 재개 가능 업로드가 필요 없다.
    board_attachment_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    board_attachment_max_count: int = Field(default=5, ge=1)

    # --- 대화형 질의 (services/chat) ---
    chat_enabled: bool = True
    # 비워 두면 wiki_llm_model 을 따른다. 인덱싱과 채팅이 같은 모델을 쓰는 것이 기본이고,
    # 다른 모델을 붙여 비교하고 싶을 때만 명시한다(예: 사내 Qwen 보조 모델).
    chat_llm_model: str = ""
    # **인덱싱의 low 와 달라야 한다.** 툴을 고르는 일은 생성이 아니라 판정이고, Solar-Open2 는
    # 판정에서 low 면 오판한다(wiki_llm.py 모듈 주석의 실측). 빈 문자열이면 파라미터 자체를
    # 보내지 않는다 — reasoning 모델이 아닌 provider 로 갈아탈 때의 탈출구다.
    chat_llm_reasoning_effort: str = "medium"
    # 툴 왕복 상한. 모델이 검색만 반복하다 끝나지 않게 하는 하드 캡이며, 도달하면 그때까지
    # 모은 근거로 답변을 강제한다.
    chat_max_tool_iterations: int = Field(default=6, ge=1, le=20)
    # 에이전트에 넘길 최근 대화 턴 수(1턴 = 질문+답변). 맥락과 프롬프트 크기의 절충이다.
    chat_history_turns: int = Field(default=8, ge=0, le=50)

    @model_validator(mode="after")
    def _forbid_insecure_secrets(self) -> Self:
        """운영(`ENVIRONMENT=production`)에서 개발용 비밀키를 쓰면 기동을 거부한다.

        기본값이 **동작하는 값**이라는 게 문제였다 — `JWT_SECRET` 을 빠뜨린 배포가 조용히 뜨고,
        그 순간 서명키가 공개 저장소에 적힌 문자열이라 누구나 access 토큰을 위조할 수 있다.
        가용성보다 이쪽이 위험하므로 fail-open 하지 않는다(rate limit 과 반대 판단이다 —
        그건 남용 완화 장치이고 이건 인가의 뿌리다).

        길이 하한을 함께 보는 이유는 placeholder 만 피한 짧은 키도 같은 문제를 남기기 때문이다.
        HS256 은 키를 블록 크기로 패딩하므로 32바이트 미만은 엔트로피가 그만큼 모자란다.
        개발 환경은 그대로 둔다 — 여기서 막으면 clone 후 곧바로 뜨지 않는다.
        """
        if self.environment.strip().lower() != "production":
            return self

        insecure = [
            name
            for name, value in (
                ("JWT_SECRET", self.jwt_secret),
                ("MINIO_SECRET_KEY", self.minio_secret_key),
                ("DATABASE_URL", self.database_url),
            )
            if PLACEHOLDER_SECRET in value
        ]
        if insecure:
            raise InsecureSecretError(
                "운영 환경에 개발용 기본 비밀키가 남아 있습니다: "
                + ", ".join(insecure)
                + ". 배포 전에 각각 무작위 값으로 교체하십시오."
            )

        if len(self.jwt_secret.encode()) < MIN_JWT_SECRET_BYTES:
            raise InsecureSecretError(
                f"JWT_SECRET 이 너무 짧습니다({len(self.jwt_secret.encode())}바이트). "
                f"{MIN_JWT_SECRET_BYTES}바이트 이상이어야 합니다 "
                "(예: `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
