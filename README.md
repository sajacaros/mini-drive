# Flex Drive

사내 구성원 대상 **경량 구글 드라이브** — 파일 업로드·다운로드·버전 관리·공유 URL 생성·그룹 기반 권한 관리를 웹으로 제공하며, 운영 전체를 Docker 로 재현 가능하게 구성한 사내 자가 호스팅 파일 공유/관리 서비스입니다. 여기에 개인 생산성 도구인 **데일리 투두 / 반복 루틴 / 리포트**를 함께 제공합니다.

설계 근거와 결정 이력은 [`spec/minidrive-prd.md`](spec/minidrive-prd.md) 에 기록돼 있습니다 — 초기 요구사항 명세에서
**구현된 것의 설계 근거 기록**으로 역할이 바뀌었으며, 코드 주석이 `PRD 3.2` 처럼 절 번호로 이 문서를 참조합니다.
기능의 현재 동작·운영 절차는 이 README 가 최신입니다.

> 브랜드명은 Flex Drive 이지만, 내부 식별자(DB·버킷·네트워크·compose 프로젝트 등)는 초기 이름인 `minidrive` / `mini-drive` 를 그대로 사용합니다.

## 주요 기능

### 드라이브

- **인증/가입**: JWT(access + refresh 회전, Redis 폐기), argon2 해싱. 가입은 **관리자 발급 가입 코드**로만 가능하며 코드 검증 즉시 활성화됩니다(만료일·사용 횟수 제한, 원자적 소모).
- **첫 부팅 셋업 위저드**: admin 이 없으면 `/setup` 에서 첫 관리자 계정 + 초기 가입 코드 + 기본 할당량을 한 번에 설정하고 셋업이 잠깁니다. 비상 복구용 CLI `python -m app.cli create-admin` 제공.
- **파일 관리**: 드래그 앤 드롭 스트리밍 업로드(최대 10 GB), 폴더, 목록/그리드(썸네일) 뷰, 휴지통(소프트 삭제)/영구 삭제, 사용자별 저장 용량 할당량(DB 원자 갱신).
- **휴지통 보존 기간**: 버린 지 `TRASH_RETENTION_DAYS` 가 지난 항목을 전용 사이드카(`purger`)가 하루 1회 정해진 시각(KST)에 영구 삭제하고 용량을 회수합니다. 휴지통 목록은 항목마다 남은 기간(`3일 후 삭제`)을 보여주며, 정리 결과는 SSE 로 열려 있는 화면에 즉시 반영됩니다. **기본값 7일(활성)** — 끄려면 `TRASH_RETENTION_DAYS=0`, 기존 스택 업그레이드 시 주의사항은 [운영](#휴지통-보존-기간-자동-영구-삭제) 참조.
- **ZIP 다운로드**: 폴더는 하위 구조 그대로, 여러 항목은 체크박스(Ctrl/Cmd·Shift 클릭도 지원)로 골라 한 번에 내려받습니다. 서버가 MinIO 에서 읽은 청크를 그대로 무압축 ZIP 프레임에 실어 스트리밍하므로 임시 파일을 만들지 않습니다(한 번에 파일 500개 / 2 GB 까지).
- **통합 드라이브**: 내 드라이브 루트 상단에 **가상 "공유" 폴더**를 고정 노출해, 내 파일과 그룹으로 공유받은 항목을 하나의 브레드크럼(내 드라이브 > 공유 > 폴더)으로 탐색합니다. 목록 뷰에 소유자/그룹/권한 컬럼을 표시하고, 행별 액션은 그 항목의 유효 권한으로 게이팅합니다.
- **실시간 반영·즐겨찾기·최근 항목**: 파일 변경을 SSE(`/api/files/events`)로 푸시해 목록이 새로고침 없이 갱신되고, 즐겨찾기(★)와 최근 열람 항목을 별도 화면으로 제공합니다.
- **재개 가능 업로드**: 1 GiB 초과 파일은 S3 Multipart 기반 청크 업로드 — 진행률/일시정지/취소, 중단(새로고침 포함) 후 이어올리기.
- **미리보기·썸네일**: 이미지/PDF/텍스트 미리보기 모달, 이미지 업로드 시 썸네일 자동 생성(`thumbnails/{fileId}.png`).
- **버전 관리**: 업로드마다 버전 기록, 히스토리 조회, 특정 버전 다운로드, 이전 버전 복구(새 버전으로 생성 — 이력 보존), `baseVersion` 충돌 감지(409).
- **공유 링크**: 만료일·비밀번호·다운로드 횟수 제한, 비활성화 즉시 410 차단(게이트웨이 모델), 공개 미리보기, 접근 통계(조회 수·마지막 접근).
- **그룹 기반 권한**: 그룹/멤버 관리(이메일·이름 검색 초대), 폴더 권한 부여와 하위 상속(조회 시 recursive CTE 판정 + Redis 캐시 — 권한 변경 즉시 반영), 권한 재정의, 그룹 소유권 이전.
- **프로필**: 우상단 프로필 칩 → 모달에서 표시 이름·비밀번호 변경, 아바타 업로드(클라이언트 canvas 중앙 크롭 → 512×512 webp 변환). 아바타는 인증 fetch 로만 조회됩니다.
- **시스템 관리자**: 사용자 관리(활성/비활성·할당량·role·표시 이름), 가입 코드 관리, 전체 그룹/공유 링크 통제, 스토리지 통계, 감사 로그. `super_admin` / `admin` 2단계 역할. **admin 도 파일 내용에는 접근 불가**(메타데이터만).

### 위키 (문서 질의)

- **인덱싱 토글**: 파일/폴더에 위키를 켜면 문서를 **절 단위 트리**로 색인해 질의 대상으로 만듭니다. 폴더에 켜면 하위로 상속되고, 파일에서 명시적으로 끄면 그 항목만 빠집니다(소유자 탈출구).
- **질의**: 자연어로 물으면 **근거(파일·절·줄 번호)와 함께** 답합니다. 근거를 누르면 해당 문서 미리보기가 열립니다.
- **권한이 곧 검색 범위**: 위키는 권한 체계를 새로 만들지 않습니다. 검색 대상은 **내가 열람할 수 있는 문서뿐**이며, 필터가 대상 선정 단계에 걸리므로 권한 없는 문서의 본문은 모델 컨텍스트에 들어가지도 않습니다.
- **전사 공개**: `@전사` 시스템 그룹에 읽기 권한을 주는 것과 같습니다. 인덱싱과 **독립**이라 PDF·pptx 처럼 색인할 수 없는 형식도 전사 공유할 수 있습니다.
- **대상 형식**: Markdown·HTML (HTML 은 제목 계층을 보존해 md 로 변환). 그 외 형식은 토글이 비활성으로 뜨고 이유를 표시합니다.
- **사내 LLM 전용**: 문서 본문은 사내 vLLM 으로만 나갑니다. 외부 API 를 쓰지 않습니다.

### 할 일

- **데일리 투두**: 날짜별 할 일 CRUD, 완료/건너뜀 상태, Pointer Events 기반 드래그 정렬(마우스·터치 통합).
- **반복 루틴**: 매일 또는 특정 요일 루틴을 등록하면, **크론 없이** 해당 날짜를 처음 조회하는 시점에 그날의 할 일로 물질화(materialize)됩니다. 기준일은 KST, `(user, date, routine)` 부분 유니크로 중복 물질화를 차단합니다.
- **주별·월별 리포트**: 완료율(분모에서 skipped 제외)·streak·일별 추이·루틴별 달성률.

### 공통

- **UI 테마 4종**: 모던/게임보이 × 다크/라이트.
- **운영**: 구조화 로깅(structlog), Prometheus 메트릭, rate limiting(fail-open), 백업/복원 스크립트, 프로덕션 compose 프로파일, 서브패스(`/drive`) 배포 지원.

## 아키텍처 개요

```
브라우저 ─▶ nginx(게이트웨이, :80) ─┬─▶ /        → frontend (React SPA)
                                    ├─▶ /api/    → backend (FastAPI)
                                    └─▶ /_minio/ → MinIO (X-Accel-Redirect 내부 전용)
                                                    backend ─▶ PostgreSQL / Redis / MinIO

사이드카(같은 이미지, entrypoint 만 다름)
  purger        하루 1회  휴지통 영구 삭제 + 위키 트리 유예 삭제
  wiki-indexer  큐 구동   Redis 큐를 보고 문서를 색인 → 사내 vLLM
```

- **게이트웨이 다운로드 모델**: 브라우저에 presigned URL 을 직접 발급하지 않고, FastAPI 가 매 요청 인가 후 `X-Accel-Redirect` 로 nginx→MinIO 스트리밍합니다. 공유 링크 비활성화 시 즉시 차단됩니다. 헤더를 실을 수 없는 브라우저 대용량 다운로드는 **일회성 다운로드 티켓**(Redis, TTL 60초, GETDEL 원자 소비)으로 인가합니다.
  - 예외는 **ZIP 다운로드** 하나입니다 — `X-Accel-Redirect` 는 오브젝트 하나만 흘려보낼 수 있어, 여러 오브젝트를 한 파일로 묶는 동안에는 backend 가 스트리밍 주체가 됩니다(`X-Accel-Buffering: no` 로 nginx 버퍼링을 끕니다). 인가는 티켓 발급 시와 소비 시 두 번 판정하며, 개수·용량 상한은 첫 바이트를 내보내기 전에 확정합니다.
- **MinIO 익명 접근 완전 차단**(`mc anonymous set none`): 게이트웨이 모델의 전제 조건입니다.
- **backend 는 호스트 포트를 노출하지 않습니다.** 모든 트래픽은 nginx 게이트웨이를 경유합니다.
- **경로를 이미지에 굽지 않습니다**: 프론트는 Vite `base=/__BASE__/` 플레이스홀더로 빌드되고, 컨테이너 기동 시 `BASE_PATH` 로 치환됩니다. 같은 이미지가 로컬(`/`)과 서버(`/drive/`)에서 재빌드 없이 동작합니다.

## 기술 스택

| 계층 | 기술 |
|---|---|
| Frontend | React 19, TypeScript 5, Tailwind CSS 4, React Router 7, Zustand 5, Axios, Vite 6 |
| Backend | Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 (async/asyncpg), Alembic, PyJWT, argon2 |
| Storage | MinIO (S3 호환) |
| Database | PostgreSQL 16 (`pgvector/pgvector:pg16` 이미지 — 기존 볼륨 호환 유지용, 확장은 미사용) |
| Cache/Token | Redis 7 |
| Gateway | nginx 1.27 |
| E2E | Playwright |

## 빠른 시작 (Docker Compose)

전체 스택을 단일 명령으로 기동합니다.

```bash
# 1. 환경변수 준비 (시크릿을 실제 값으로 교체)
cp .env.example .env

# 2. 빌드 + 기동
docker compose up -d --build

# 3. 상태 확인
docker compose ps
curl http://localhost/health     # {"status":"ok","database":"ok","minio":"ok","redis":"ok"}
```

- 웹 UI: <http://localhost/>
- API 문서 (Swagger): <http://localhost/api/docs>
- 종료: `docker compose down` (볼륨까지 삭제하려면 `docker compose down -v`)

### 첫 부팅 셋업

빈 DB 로 처음 기동하면 관리자가 없으므로 웹 UI 가 **셋업 위저드(`/setup`)** 로 안내합니다.
여기서 첫 관리자 계정, 초기 가입 코드, 신규 가입자 기본 할당량을 설정하면 셋업이 영구 잠깁니다.

1. `http://localhost/` 접속 → `/setup` 으로 유도
2. 관리자 이메일/비밀번호 + 기본 할당량 입력 → 초기 가입 코드 발급
3. 구성원에게 가입 코드 전달 → 가입 즉시 로그인 가능 (승인 대기 없음)
4. 추가 코드 발급/비활성화는 admin 대시보드의 **가입 코드 관리**에서

관리자 계정을 잃어버린 경우 비상 복구:

```bash
docker compose exec backend python -m app.cli create-admin --email you@example.com
```

> nginx 설정을 변경한 경우 inode 교체 문제를 피하려면 컨테이너를 재생성하세요:
> `docker compose up -d --force-recreate nginx`

## 운영 (프로덕션)

프로덕션은 base compose 에 `docker-compose.prod.yml` 오버라이드를 얹어 기동합니다.
오버라이드는 `restart: unless-stopped`, JSON 구조화 로깅(`LOG_FORMAT=json`), 도커 로그 로테이션
(`json-file` max-size/max-file), 리소스 limits, `DEBUG=false` 를 적용합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### 서버 배포 (기존 nginx 뒤 서브패스 `/drive`)

이미 80/443 을 처리하는 호스트 nginx 뒤에 `https://<host>/drive` 로 붙이는 구성은
[`deploy/DEPLOY.md`](deploy/DEPLOY.md) 에 단계별로 정리돼 있습니다. 배포 호스트에서 직접 빌드하며
(레지스트리 없음), 게이트웨이는 `127.0.0.1:7755` 에서만 대기하고 호스트 nginx 가 프록시합니다.

```bash
cd deploy
cp .env.deploy.example .env && chmod 600 .env    # BASE_PATH=/drive/ · 시크릿 교체
docker compose -f docker-compose.deploy.yml up -d --build
curl -fsS http://127.0.0.1:7755/health && echo OK
```

### 로깅 (구조화 로깅, PRD 11장)

- 요청마다 미들웨어가 `request_id`(수신 `X-Request-ID` 우선, 없으면 생성), `method`, `path`,
  `path_template`(카디널리티 안전), `status`, `duration_ms`, 인증 시 `user_id`, `client_ip` 를
  한 줄 로그로 남기며, 응답에 `X-Request-ID` 헤더를 되돌려줍니다. uvicorn access 로그는 중복이라 끕니다.
- 운영은 JSON(`LOG_FORMAT=json`), 개발은 컬러 콘솔(`LOG_FORMAT=console`). 레벨은 `LOG_LEVEL`.

```bash
# 실시간 로그 (특정 서비스)
docker compose logs -f backend

# 특정 request_id 로 요청 추적
docker compose logs backend | grep '"request_id": "<id>"'
```

### 메트릭 (Prometheus, PRD 11장)

백엔드가 `GET /metrics` 로 Prometheus 지표를 노출합니다:
`http_requests_total{method,path,status}`, `http_request_duration_seconds`(히스토그램),
`minidrive_upload_bytes_total`, `minidrive_download_bytes_total`,
`rate_limit_rejections_total{scope}`, `minidrive_trash_purged_total`,
`minidrive_trash_purged_bytes_total`. `path` 라벨은 실제 경로가 아닌 **라우트 템플릿**입니다.

`/metrics` 는 **게이트웨이(nginx)에서 외부 접근을 차단**(`deny all` → 403)하며, 내부 도커
네트워크의 스크레이퍼만 `backend:8000/metrics` 로 접근합니다. Prometheus 스크레이프 예:

```yaml
scrape_configs:
  - job_name: minidrive-backend
    metrics_path: /metrics
    static_configs:
      - targets: ["backend:8000"]   # minidrive 네트워크 내부에서 실행 시

  # 휴지통 정리 카운터는 사이드카 프로세스에 쌓이므로 별도 대상입니다(backend 에는 안 보입니다).
  - job_name: minidrive-purger
    metrics_path: /metrics
    static_configs:
      - targets: ["purger:8000"]
```

```bash
# 내부에서 스크레이프 확인
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode()[:500])"

# 외부(게이트웨이)에서는 차단됨 → 403
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/metrics
```

### 휴지통 보존 기간 (자동 영구 삭제)

휴지통에 버린 지 `TRASH_RETENTION_DAYS` 일이 지난 항목을 **`purger` 사이드카**가 매일
`TRASH_PURGE_HOUR`(KST, 기본 4시)에 영구 삭제하고 용량을 회수합니다. 설계 근거는
[`spec/trash-retention-purge.md`](spec/trash-retention-purge.md) 에 있습니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `TRASH_RETENTION_DAYS` | `7` | 보존 일수. **설정하지 않아도 자동 정리가 켜집니다.** 끄려면 `0` 을 명시하세요 |
| `TRASH_PURGE_HOUR` | `4` | 실행 시각(KST, 0~23). 벽시계 기준이라 컨테이너를 재시작해도 시각이 밀리지 않습니다 |

- 사이드카는 backend 와 **같은 이미지**를 쓰고 entrypoint 만 덮으므로 빌드가 늘지 않습니다
  (마이그레이션은 backend 만 실행합니다).
- 삭제는 사용자가 휴지통에서 누르는 영구 삭제와 **같은 코드 경로**를 타므로, 소유자별 할당량
  회수·공유 링크 정리·썸네일 삭제가 동일하게 적용됩니다.
- 정리 결과는 SSE 로 발행되어 열려 있는 휴지통 화면이 새로고침 없이 갱신됩니다. 목록에는
  항목별 남은 기간(`3일 후 삭제`)이 표시됩니다.

> ⚠️ **이미 운영 중인 스택을 업그레이드할 때 주의.** 기본값이 켜져 있으므로, 별도 설정 없이
> 올리면 **첫 회차가 7일이 지난 기존 휴지통 항목을 한꺼번에 영구 삭제**합니다. 되돌릴 수
> 없으니 올리기 전에 규모를 확인하고, 원치 않으면 `.env` 에 `TRASH_RETENTION_DAYS=0` 을
> 먼저 넣으세요.

```bash
# 1. 규모 확인 — 아무것도 지우지 않고 대상 건수와 회수될 용량만 출력 (건별 로그 포함)
#    (아직 안 켠 스택이라면 이 명령에만 값을 주면 됩니다: -e TRASH_RETENTION_DAYS=7)
docker compose exec backend python -m app.cli purge-trash --dry-run

# 2. 그대로 두면 기본 7일로 켜집니다. 끄려면 .env 에 TRASH_RETENTION_DAYS=0 후 재기동
docker compose up -d backend purger

# 3. 즉시 1회 실행 (다음 실행 시각까지 기다리지 않고 검증할 때)
docker compose exec backend python -m app.cli purge-trash --once

# 4. 동작 확인 — 다음 실행 시각과 회차 결과가 한 줄씩 남습니다
docker compose logs -f purger
```

수동 실행(`--once`)과 사이드카가 겹쳐도 안전합니다 — 회차 전체를 Redis 리스로 감싸며, 잠금을
잡지 못한 쪽은 조용히 건너뜁니다. `TRASH_RETENTION_DAYS=0` 이면 사이드카는 그 사실을 한 번
로그로 남기고 유휴 대기합니다(재시작 루프 방지).

> 자동 삭제 이력은 **컨테이너 로그에만** 남고 감사 로그(`audit_logs`)에는 기록하지 않습니다 —
> 그 테이블은 행위자(`actor_id`)가 필수인 사람 행위 기록이기 때문입니다. 로그는 로테이션되므로
> 장기 보관이 필요하면 로그 수집기로 내보내세요.

### 위키 인덱싱

Markdown·HTML 문서를 절 단위 트리로 색인해 질의할 수 있게 합니다. 설계 근거와 실측은
[`spec/wiki-index.md`](spec/wiki-index.md) 에 있습니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `WIKI_ENABLED` | `true` | 끄면 사이드카가 유휴 대기하고 토글 API 가 503 을 냅니다 |
| `WIKI_LLM_BASE_URL` | 사내 vLLM | OpenAI 호환 엔드포인트. **문서 본문이 이 주소로만 나갑니다** |
| `WIKI_LLM_API_KEY` | (빈 값) | 미설정 시 401 — 색인은 되지만 요약이 본문 앞부분으로 대체됩니다 |
| `WIKI_LLM_MODEL` | `hosted_vllm/solar-open2-250b` | `hosted_vllm/` 프리픽스는 떼고 전송합니다 |
| `WIKI_LLM_REASONING_EFFORT` | `low` | 생성 계열은 `low` 로 충분합니다(아래) |
| `WIKI_MAX_INPUT_BYTES` | `2MB` | 초과 파일은 토글이 비활성됩니다 |
| `WIKI_PURGE_GRACE_DAYS` | `30` | 위키를 끈 뒤 트리를 실제로 지우기까지의 유예. `0` 이면 다음 회차에 삭제 |

- **큐 구동**입니다(스케줄 아님). Redis 정렬 집합 하나로 큐·디바운스(10초)·합치기를 함께
  처리하므로, 연속 버전업이 색인 한 번으로 접힙니다.
- 상태는 `off → pending → indexing → ready`, 새 버전이 오면 `stale`, 실패는 `failed` 입니다.
  **인덱싱 중에도 이전 트리로 계속 답합니다** — 문서를 검색에서 빼면 답변이 누락되기 때문입니다.
- 토글을 켜면 DB 에 `pending` 행이 먼저 생깁니다. Redis 큐가 유실돼도(flush·재시작) 사이드카가
  기동 시 고아를 찾아 다시 넣습니다.
- **`reasoning_effort` 는 낮게 씁니다.** Solar-Open2 는 추론 모델이라 기본값에서 호출당 수백~수천
  토큰을 태웁니다(실측: 요약 1건 11.4초/911토큰 → `low` 는 0.7초/44토큰). 생성 품질은 유지되지만
  판정 계열은 `low` 에서 오판하므로, 판정이 필요한 곳은 호출부에서 올립니다.
- GPU 를 대화형 질의와 나눠 쓰므로 색인 동시성은 낮게(3) 잡혀 있습니다.

### 백업 / 복원 (PRD 12장)

실행 중인 스택을 대상으로 PostgreSQL(pg_dump custom format) + MinIO 버킷(mc mirror) 을 백업합니다.
산출물은 `backups/{timestamp}/` 에 저장되고, 최근 N개(기본 7, `BACKUP_RETENTION`)만 보존합니다.
`backups/` 는 `.gitignore` 로 제외됩니다. 자격 증명은 `.env` 에서 읽습니다.

```bash
# 백업 (스택이 up 상태여야 함)
scripts/backup.sh
BACKUP_RETENTION=14 scripts/backup.sh      # 보존 개수 조정

# 복원 (지정 백업으로 현재 스택을 되돌림 — 확인 프롬프트)
scripts/restore.sh backups/20260718-120000
scripts/restore.sh backups/20260718-120000 --yes   # 프롬프트 생략
```

> cron 등록 예(매일 03:00): `0 3 * * * cd /path/to/flex-drive && scripts/backup.sh >> /var/log/minidrive-backup.log 2>&1`

### TLS (443)

`docker-compose.prod.yml` 의 nginx 서비스에 인증서 마운트 지점과 자가서명 예시 명령이
주석으로 안내되어 있습니다. 인증서를 `nginx/certs/` 에 배치하고 볼륨 주석을 해제한 뒤,
`nginx/conf.d` 에 443 server 블록을 추가하세요. 기존 nginx 뒤에 붙이는 배포에서는 TLS 를
호스트 nginx 가 종단하므로 이 설정이 필요 없습니다([`deploy/DEPLOY.md`](deploy/DEPLOY.md)).

## 개발 환경 셋업

### 백엔드 (backend/)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # 또는: pip install -r requirements.txt

# DB/MinIO/Redis 는 compose 로 띄워두고 backend 만 로컬 실행하려면:
#   docker compose up -d db minio mc redis
# .env 의 호스트명(db, minio, redis)을 localhost 로 바꾸거나 별도 설정이 필요합니다.
uvicorn app.main:app --reload --port 8000
```

- 헬스체크: <http://localhost:8000/health>
- API 문서: <http://localhost:8000/docs>

### 프론트엔드 (frontend/)

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173 (‑> /api 는 :8000 으로 프록시)
```

### 테스트

유닛 테스트는 로컬에서 실행합니다:

```bash
cd backend && pytest
```

통합 테스트(`backend/tests/integration_*.py`)는 **compose 스택이 떠 있는 상태에서, compose
네트워크 내부의 일회성 컨테이너**로 실행합니다. 런타임 이미지에는 dev 의존성이 없으므로
소스를 마운트하고 httpx 를 임시 설치합니다:

```bash
docker compose run --rm \
  -v "$(pwd)/backend/app:/app/app" -v "$(pwd)/backend/tests:/app/tests" \
  -v "$(pwd)/backend/alembic:/app/alembic" \
  -e RATE_LIMIT_ENABLED=false \
  --entrypoint sh backend -c "pip install -q httpx && python -m tests.integration_files"
```

- `integration_admin` 은 rate limit 동작 자체를 검증하므로 `RATE_LIMIT_ENABLED` 를 끄지 않고 실행합니다.
- `integration_resumable` 은 다청크 검증을 위해 `-e RESUMABLE_PART_SIZE=5242880` 을 함께 줍니다.
- `alembic/` 도 함께 마운트하세요. 테스트는 스키마를 재생성한 뒤 head 로 stamp 하므로, 이미지에
  없는 새 리비전이 있으면 `Can't locate revision` 으로 실패합니다.
- **주의: 통합 테스트는 파괴적입니다** — dev DB/버킷을 초기화합니다. 운영 데이터가 있는 곳에서 실행하지 마세요.
- 테스트가 `DROP TABLE` 에서 멈춘다면 **열려 있는 브라우저 탭**을 닫아 보세요. SSE 스트림
  (`/api/files/events`)이 살아 있는 동안 DB 세션이 `idle in transaction` 으로 남아 DDL 을 막습니다.

E2E(Playwright)는 기동된 게이트웨이(`http://localhost`)를 브라우저로 검증합니다 — SSE 실시간
갱신, 즐겨찾기, 최근 항목 시나리오:

```bash
cd frontend
npx playwright install --with-deps chromium   # 최초 1회
npx playwright test                            # E2E_BASE_URL 로 대상 변경 가능
```

> 스위트를 통째로 돌릴 때는 **rate limit 을 끈 스택**(`RATE_LIMIT_ENABLED=false`)을 대상으로 하세요.
> 업로드는 사용자당 분당 10회라 업로드가 잦은 스펙들이 연달아 돌면 429 로 실패합니다.

## 디렉터리 구조

```
flex-drive/
├── backend/                 # FastAPI 백엔드
│   ├── app/
│   │   ├── main.py          # 앱 팩토리 + /health + 로깅/메트릭 미들웨어
│   │   ├── cli.py           # 운영 CLI (admin 생성, purge-trash, index-wiki)
│   │   ├── core/            # config, database, redis, security
│   │   ├── api/routes/      # auth, setup, users, files, shares, groups, permissions, admin, todos, wiki
│   │   ├── services/        # 도메인 서비스 계층 (files, permissions, todos, wiki* …)
│   │   ├── models/ schemas/ # SQLAlchemy 모델 / Pydantic 스키마
│   │   └── ...
│   ├── alembic/             # DB 마이그레이션
│   ├── tests/               # 유닛 + 통합 테스트 (integration_*.py)
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                # React SPA
│   ├── src/
│   │   ├── api/             # axios 클라이언트 + 도메인 API
│   │   ├── components/      # Avatar, PageHeader, PreviewModal, ProfileModal 등
│   │   ├── lib/             # 재개 업로드·SSE·아바타 크롭·basePath 등
│   │   └── pages/           # 드라이브 / 할 일 / 관리 화면
│   ├── e2e/                 # Playwright E2E (favorites, recent, sse)
│   ├── docker-entrypoint.d/ # 기동 시 BASE_PATH 치환 스크립트
│   ├── Dockerfile           # multi-stage: node build → nginx serve
│   └── nginx.conf
├── nginx/                   # 게이트웨이
├── deploy/                  # 서브패스 배포 자산 (DEPLOY.md, compose, nginx snippet)
├── scripts/                 # backup.sh / restore.sh
├── spec/                    # PRD(설계 근거 기록) + 페이즈 설계 문서
├── docker-compose.yml
├── docker-compose.prod.yml  # 프로덕션 오버라이드
└── .env.example
```

## 개발 마일스톤 (PRD 9절)

| Phase | 내용 | 상태 |
|---|---|---|
| **1. MVP** | 인증 + 파일 업로드/다운로드/목록 + 공유 링크 + 최소 admin | ✅ |
| **2. 버전 관리** | 버전 히스토리 / 복구 / 충돌 감지 | ✅ |
| **3. 그룹 & 권한** | 그룹 CRUD, 폴더 권한 상속(조회 시 판정 + Redis 캐시) | ✅ |
| **4. 운영 안정성 + Admin** | 게이트웨이 정제, 로깅/메트릭, rate limiting, 백업, admin 대시보드 | ✅ |
| **5. 고도화** | 재개 업로드, 썸네일, 미리보기, 공유 통계, UI 테마 4종 | ✅ |
| **6. 셋업 위저드 + 가입 코드제** | 첫 부팅 셋업, 가입 승인제 → 가입 코드제 전환 | ✅ |
| **7. LLM 위키 & 챗봇** | RAG 인덱싱 / 챗봇 / 전사 위키 | ⛔ 제거됨(2026-07-19 — 파일 공유 코어 집중, git 이력 보존). 2026-07-28 에 **벡터 없이** 재설계해 되살렸습니다 — 아래 「위키」 참조 |
| **8. 드라이브 UX** | 파일 변경 SSE, 즐겨찾기, 최근 항목 ([`spec/drive-ux-phase8.md`](spec/drive-ux-phase8.md)) | ✅ |

PRD 마일스톤 이후 추가된 작업(설계 문서 없이 커밋 단위로 진행):

| 작업 | 내용 | 상태 |
|---|---|---|
| **서브패스 배포** | 런타임 `BASE_PATH` 주입, `deploy/` 배포 자산 | ✅ |
| **통합 드라이브 + 프로필** | 가상 "공유" 폴더·권한 컬럼, 프로필 모달·아바타, `super_admin` 역할 | ✅ |
| **할 일** | 데일리 투두, 반복 루틴 물질화, 주별·월별 리포트 | ✅ |
| **위키** | 절 단위 트리 색인 + 근거 있는 질의. 벡터 DB 없이 사내 vLLM 만 사용 ([`spec/wiki-index.md`](spec/wiki-index.md)) | ✅ |

델타 동기화는 웹 중심 서비스 특성상 범위에서 제외되었습니다(PRD 3.5).
