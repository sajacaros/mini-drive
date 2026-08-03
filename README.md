# Flex Drive

사내 구성원 대상 **경량 구글 드라이브** — 파일 업로드·다운로드·버전 관리·공유 링크·그룹 권한을
웹으로 제공하고, 전체 운영을 Docker 로 재현할 수 있게 구성한 자가 호스팅 파일 공유 서비스입니다.
여기에 문서 질의(**위키·채팅**)와 개인용 **할 일** 도구를 함께 제공합니다.

문서를 읽고 답하는 쪽은 전부 **사내 vLLM 에 올린 [Solar-Open2-250B](https://huggingface.co/upstage)**
가 맡습니다. 색인·질의·대화·툴 호출이 모두 이 모델 하나로 돌고, **문서 본문은 사내망 밖으로 나가지
않습니다.** 외부 LLM API 를 쓰는 경로는 없습니다.

## 주요 기능

### 드라이브

- **인증/가입**: JWT(access + refresh 회전, Redis 폐기), argon2 해싱. 가입은 **관리자 발급 가입
  코드**로만 가능합니다.
- **첫 부팅 셋업**: admin 이 없으면 `/setup` 에서 첫 관리자·초기 가입 코드·기본 할당량을 한 번에
  정하고 잠깁니다. 복구용 CLI `python -m app.cli create-admin` 제공.
- **파일 관리**: 드래그 앤 드롭 스트리밍 업로드(최대 10 GB), 폴더, 목록/그리드 뷰, 휴지통, 사용자별
  용량 할당량.
- **휴지통 자동 정리**: `TRASH_RETENTION_DAYS`(기본 7일)가 지난 항목을 `purger` 사이드카가 하루 1회
  영구 삭제하고 용량을 회수합니다.
- **ZIP 다운로드**: 폴더는 하위 구조 그대로, 여러 항목은 체크박스로 골라 한 번에. MinIO 청크를
  무압축 ZIP 프레임에 실어 스트리밍하므로 임시 파일이 없습니다(파일 500개 / 2 GB 상한).
- **통합 드라이브**: 내 드라이브 루트에 가상 "공유" 폴더를 고정해, 내 파일과 공유받은 항목을 하나의
  브레드크럼으로 탐색합니다. 행별 액션은 해당 항목의 유효 권한으로 게이팅합니다.
- **실시간 반영**: 파일 변경을 SSE(`/api/files/events`)로 푸시해 목록이 새로고침 없이 갱신됩니다.
  즐겨찾기·최근 항목 화면 별도 제공.
- **재개 가능 업로드**: 1 GiB 초과 파일은 S3 Multipart 청크 업로드 — 진행률/일시정지/취소, 새로고침
  후 이어올리기.
- **미리보기·썸네일**: 이미지/PDF/텍스트 미리보기, 이미지 업로드 시 썸네일 자동 생성.
- **버전 관리**: 업로드마다 버전 기록, 히스토리, 특정 버전 다운로드, 이전 버전 복구(새 버전으로
  생성), `baseVersion` 충돌 감지(409).
- **공유 링크**: 만료일·비밀번호·다운로드 횟수 제한, 비활성화 즉시 410 차단, 공개 미리보기, 접근
  통계.
- **그룹 권한**: 그룹/멤버 관리, 폴더 권한과 하위 상속(조회 시 recursive CTE 판정 + Redis 캐시),
  권한 재정의, 소유권 이전.
- **시스템 관리자**: 사용자·가입 코드·그룹·공유 링크 통제, 스토리지 통계, 감사 로그. `super_admin` /
  `admin` 2단계. **admin 도 파일 내용에는 접근할 수 없습니다**(메타데이터만).

### 위키 (문서 질의)

- **인덱싱 토글**: 파일/폴더에 위키를 켜면 문서를 **절 단위 트리**로 색인합니다. 폴더에 켜면 하위로
  상속되고, 파일에서 끄면 그 항목만 빠집니다.
- **질의**: 자연어로 물으면 **근거(파일·절·줄 번호)와 함께** 답합니다. 근거는 접힌 채로 오되 건수는
  드러내고, 펴서 누르면 해당 문서의 그 위치가 미리보기로 열립니다.
- **권한이 곧 검색 범위**: 위키는 권한 체계를 새로 만들지 않습니다. 대상은 **내가 열람할 수 있는
  문서뿐**이고, 필터가 대상 선정 단계에 걸리므로 권한 없는 문서 본문은 모델 컨텍스트에 들어가지
  않습니다.
- **전사 공개**: `@전사` 시스템 그룹에 읽기 권한을 주는 것과 같습니다. 인덱싱과 독립이라 PDF·pptx 도
  전사 공유는 가능합니다.
- **대상 형식**: Markdown·HTML. 그 외는 토글이 비활성으로 뜨고 이유를 표시합니다.
- **사내 LLM 전용**: 문서 본문은 사내 vLLM(Solar-Open2-250B)으로만 나갑니다. 외부 API 를 쓰지
  않습니다.

### 채팅 (맥락이 이어지는 질의)

- **후속 질문이 통합니다**: 세션 단위로 대화가 쌓이고, "그럼 P2 는?"처럼 **주어가 빠진 질문**도
  앞선 턴을 보고 답합니다. 모델이 대화 맥락을 **독립형 검색 질의**로 바꿔 검색하기 때문입니다.
- **답변의 형태를 모델이 고릅니다**: 견주기 좋은 자료가 나오면 "비교해줘"라고 말하지 않아도 표가
  됩니다. 형태를 고르는 것이 앞단의 분류기가 아니라 모델이라서 그렇습니다 — 아키텍처의
  「채팅 — 툴 루프」 참고. 다만 **표의 모양은 일정하지 않습니다**(같은 질문에 3행×2열도, 1행×3열도
  나옵니다).
- **검색한 질의를 드러냅니다**: 답이 엉뚱할 때 원인은 대개 "검색이 다른 걸 가져왔다"인데, 최종
  답변만 봐서는 보이지 않습니다.
- **좁혀 본 것을 숨기지 않습니다**: 문서가 많으면 관련된 절부터 예산만큼만 모델에 올리고, 그 사실을
  "N건 중 M건을 들여다봤습니다"로 말합니다. 이것을 "전부 뒤졌다"로 읽으면 "없다"는 답을 과신합니다.
- **세션 관리**: 첫 질문이 제목이 되고(보내는 즉시 목록에 반영), 제목 변경·삭제가 가능합니다.
  삭제는 소프트 삭제이며, **남의 세션은 403 이 아니라 404** 입니다 — 403 은 그 번호의 대화가
  존재한다는 사실을 흘리고, 대화 제목에는 업무 내용이 들어갑니다.
- `/wiki`(단발 질의)는 세션을 만들지 않는 경로로 남아 있습니다. 사이드바 진입점은 **채팅 하나**로
  합쳤지만, 북마크와 옛 링크가 깨지지 않게 주소는 살려 두었습니다.

### 할 일

- 날짜별 할 일 CRUD, 완료/건너뜀, 드래그 정렬.
- 반복 루틴은 **크론 없이** 해당 날짜를 처음 열어 볼 때 그날의 할 일로 만들어집니다(기준일 KST, 같은
  루틴이 두 번 생기는 것은 부분 유니크로 차단).
- 주별·월별 리포트: 완료율(분모에서 skipped 제외)·streak·루틴별 달성률.

### 공통

- UI 테마 4종(모던/게임보이 × 다크/라이트), 서브패스(`/drive`) 배포 지원.
- 구조화 로깅(structlog, `LOG_FORMAT=json|console`), Prometheus 메트릭(`GET /metrics`,
  게이트웨이에서는 403 차단), rate limiting(fail-open).

## 아키텍처

```
브라우저 ─▶ nginx(게이트웨이, :80) ─┬─▶ /        → frontend (React SPA)
                                    ├─▶ /api/    → backend (FastAPI)
                                    └─▶ /_minio/ → MinIO (X-Accel-Redirect 내부 전용)
                                                    backend ─▶ PostgreSQL / Redis / MinIO

사이드카(같은 이미지, entrypoint 만 다름)
  purger        하루 1회  휴지통 영구 삭제 + 위키 트리 유예 삭제
  wiki-indexer  큐 구동   Redis 큐를 보고 문서를 색인 → 사내 vLLM
```

- **게이트웨이 다운로드 모델**: presigned URL 을 브라우저에 주지 않고, FastAPI 가 매 요청 인가한 뒤
  `X-Accel-Redirect` 로 nginx→MinIO 스트리밍합니다. 공유 링크를 끄면 즉시 차단됩니다. 헤더를 실을 수
  없는 대용량 다운로드는 일회성 티켓(Redis, TTL 60초, GETDEL 원자 소비)으로 인가합니다.
  - 예외는 ZIP 하나입니다 — `X-Accel-Redirect` 는 오브젝트 하나만 흘려보낼 수 있어, 묶는 동안에는
    backend 가 스트리밍 주체가 됩니다. 인가는 티켓 발급·소비 두 번 판정하고, 개수·용량 상한은 첫
    바이트 전에 확정합니다.
- **MinIO 익명 접근 완전 차단**(`mc anonymous set none`)이 이 모델의 전제입니다.
- **backend 는 호스트 포트를 열지 않습니다.** 모든 트래픽이 nginx 를 지납니다.
- **경로를 이미지에 굽지 않습니다**: 프론트는 `base=/__BASE__/` 로 빌드하고 기동 시 `BASE_PATH` 로
  치환합니다. 같은 이미지가 로컬(`/`)과 서버(`/drive/`)에서 재빌드 없이 돕니다.

### 채팅 — 툴 루프

질문 하나가 도는 경로입니다.

```
POST /api/chat/sessions/{id}/messages   { question }
   │
   │  최근 턴 8개를 붙여 프롬프트를 만든다
   ▼
Solar-Open2-250B 가 다음 한 수를 고른다  ◀───────────────┐
   │                                                     │
   ├─▶ search_wiki ─▶ 위키 트리 (PostgreSQL) ─ 결과 ─────┘
   │      맥락을 독립형 질의로 바꿔 검색한다
   │
   ├─▶ answer_comparison · answer_text · answer_*
   │      artifact(kind) 로 내려간다
   │
   └─▶ 툴 없이 평문
          │
          ▼
   질문 · 답변 · 근거 · 툴 흔적을 한 트랜잭션으로 저장
   chat_sessions / chat_messages
```

**요점은 루프입니다.** 모델은 한 번에 답하지 않고, 왕복마다 "검색이 더 필요한가, 이제 답할
것인가"를 스스로 고릅니다. 그래서 답의 **형태**를 앞단에서 분류할 필요가 없습니다 — 형태를
`answer_*` 툴로 만들어 두면 모델이 검색 결과를 본 뒤 고르고, 프런트는 `kind` 로 렌더러만 고릅니다.
형태를 늘리는 일은 백엔드 클래스 하나 + 프런트 렌더러 하나로 끝납니다.

**끝나지 않는 경로가 없어야 합니다.** 왕복은 `CHAT_MAX_TOOL_ITERATIONS`(기본 6)로 막고, 도달하면
그때까지 모은 근거로 답을 강제합니다. 검색만 반복하다 빈손으로 끝나는 길을 남겨 두지 않습니다.

**툴 없이 끝나는 턴을 예외 처리하지 않습니다.** 모델은 표가 필요할 때만 렌더 툴을 부르므로, 평문으로
끝나는 쪽이 오히려 흔한 경로입니다.

**저장의 진실은 테이블 둘뿐입니다.** 그래프는 매 턴 stateless 로 돌리고 LangGraph checkpointer 를
쓰지 않습니다. 그건 에이전트 실행 상태용이지 제품 엔티티용이 아닙니다 —
`langgraph-checkpoint-postgres` 는 psycopg 3 를 끌고 들어와 asyncpg 옆에 두 번째 드라이버가 되고,
만드는 테이블 넷이 Alembic 밖이며, 대화 본문이 BYTEA 로 직렬화돼 목록의 제목·미리보기를 뽑으려면
역직렬화해야 합니다.

**LLM 접속은 `services/llm.py` 한 곳**(LiteLLM)입니다. 재시도 규율은 라이브러리 기본값에 넘기지
않고 우리가 쥡니다(`num_retries=0` — 4xx 는 재시도하지 않고 429 만 재시도). `drop_params=False`
인 것은 조용히 떨어지는 인자가 `reasoning_effort` 이기 때문입니다 — 판정이 `low` 로 돌아간 것과
구분되지 않습니다.

## 기술 스택

| 계층        | 기술                                                                                  |
| ----------- | ------------------------------------------------------------------------------------- |
| Frontend    | React 19, TypeScript 5, Tailwind CSS 4, React Router 7, Zustand 5, Vite 6             |
| Backend     | Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2(async/asyncpg), Alembic, PyJWT, argon2 |
| Storage     | MinIO (S3 호환)                                                                       |
| Database    | PostgreSQL 16 (`pgvector/pgvector:pg16` — 기존 볼륨 호환용, 확장은 미사용)            |
| Cache/Token | Redis 7                                                                               |
| Gateway     | nginx 1.27                                                                            |
| LLM         | **사내 vLLM — Solar-Open2-250B FP8**, OpenAI 호환 API, tool calling 활성              |
| LLM 클라이언트 | LiteLLM (provider 프리픽스로 교체 가능), 대화 그래프는 LangGraph                    |
| E2E         | Playwright                                                                            |

## 빠른 시작

```bash
cp .env.example .env          # 시크릿을 실제 값으로 교체
docker compose up -d --build
curl http://localhost/health  # {"status":"ok","database":"ok","minio":"ok","redis":"ok"}
```

- 웹 UI <http://localhost/> · API 문서 <http://localhost/api/docs>
- 종료: `docker compose down` (볼륨까지: `down -v`)

빈 DB 로 처음 기동하면 `/setup` 셋업 위저드로 유도됩니다. 첫 관리자 계정, 초기 가입 코드, 신규
가입자 기본 할당량을 정하면 셋업이 영구 잠깁니다. 이후 코드 발급/비활성화는 admin 대시보드에서
합니다.

관리자 계정을 잃었을 때:

```bash
docker compose exec backend python -m app.cli create-admin --email you@example.com
```

> nginx 설정을 바꿨다면 inode 교체 문제를 피하려고 컨테이너를 재생성하세요:
> `docker compose up -d --force-recreate nginx`

## 운영

프로덕션은 오버라이드를 얹어 기동합니다(`restart: unless-stopped`, JSON 로깅, 로그 로테이션, 리소스
limit, `DEBUG=false`).

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

이미 80/443 을 쓰는 호스트 nginx 뒤에 `https://<host>/drive` 로 붙이는 구성은
[`deploy/DEPLOY.md`](deploy/DEPLOY.md) 에 단계별로 있습니다(레지스트리 없이 배포 호스트에서 빌드,
게이트웨이는 `127.0.0.1:7755` 대기).

### 휴지통 보존 기간

| 환경변수               | 기본값 | 설명                                                     |
| ---------------------- | ------ | -------------------------------------------------------- |
| `TRASH_RETENTION_DAYS` | `7`    | 보존 일수. **설정하지 않아도 켜집니다.** 끄려면 `0` 명시 |
| `TRASH_PURGE_HOUR`     | `4`    | 실행 시각(KST). 벽시계 기준이라 재시작해도 밀리지 않음   |

삭제는 사용자가 누르는 영구 삭제와 같은 코드 경로라 할당량 회수·공유 링크 정리·썸네일 삭제가
동일하게 적용되고, 결과는 SSE 로 열려 있는 화면에 반영됩니다. 설계 근거는
[`spec/trash-retention-purge.md`](spec/trash-retention-purge.md).

> **운영 중인 스택을 올릴 때 주의.** 기본값이 켜져 있어 첫 회차에 7일 지난 기존 휴지통 항목을
> 한꺼번에 지웁니다. 되돌릴 수 없으니 먼저 규모를 확인하세요.

```bash
docker compose exec backend python -m app.cli purge-trash --dry-run  # 대상 건수·회수 용량만 출력
docker compose exec backend python -m app.cli purge-trash --once     # 즉시 1회 실행
docker compose logs -f purger                                        # 다음 실행 시각·회차 결과
```

수동 실행과 사이드카가 겹쳐도 안전합니다(회차 전체를 Redis 리스로 감싸고, 잠금을 못 잡은 쪽은
건너뜁니다). 자동 삭제 이력은 컨테이너 로그에만 남습니다 — `audit_logs` 는 행위자가 필수인 사람 행위
기록이기 때문입니다.

### 위키 인덱싱

| 환경변수                    | 기본값                         | 설명                                                            |
| --------------------------- | ------------------------------ | --------------------------------------------------------------- |
| `WIKI_ENABLED`              | `true`                         | 끄면 사이드카가 유휴 대기하고 토글 API 가 503                   |
| `WIKI_LLM_BASE_URL`         | 사내 vLLM                      | OpenAI 호환 엔드포인트. **문서 본문이 이 주소로만 나갑니다**    |
| `WIKI_LLM_API_KEY`          | (빈 값)                        | 미설정 시 401 — 색인은 되지만 요약이 본문 앞부분으로 대체됩니다 |
| `WIKI_LLM_MODEL`            | `hosted_vllm/solar-open2-250b` | `hosted_vllm/` 프리픽스는 떼고 전송                             |
| `WIKI_LLM_REASONING_EFFORT` | `low`                          | 생성 계열은 `low` 로 충분                                       |
| `WIKI_MAX_INPUT_BYTES`      | `2MB`                          | 초과 파일은 토글 비활성                                         |
| `WIKI_PURGE_GRACE_DAYS`     | `30`                           | 위키를 끈 뒤 트리를 지우기까지의 유예                           |

- **큐 구동**입니다(스케줄 아님). Redis 정렬 집합 하나로 큐·디바운스(10초)·합치기를 처리해 연속
  버전업이 색인 한 번으로 접힙니다.
- 상태는 `off → pending → indexing → ready`, 새 버전이 오면 `stale`, 실패는 `failed`. **인덱싱
  중에도 이전 트리로 계속 답합니다.**
- 토글을 켜면 DB 에 `pending` 이 먼저 생겨서, Redis 큐가 유실돼도 사이드카가 기동 시 고아를 찾아
  다시 넣습니다.
- GPU 를 대화형 질의와 나눠 쓰므로 색인 동시성은 3 으로 낮춰 두었습니다.

설계 근거와 실측은 [`spec/wiki-index.md`](spec/wiki-index.md).

### 대화형 질의 (채팅)

| 환경변수                      | 기본값                | 설명                                                        |
| ----------------------------- | --------------------- | ----------------------------------------------------------- |
| `CHAT_ENABLED`                | `true`                | 끄면 채팅 API 가 503                                        |
| `CHAT_LLM_MODEL`              | (빈 값)               | 비우면 `WIKI_LLM_MODEL` 을 따릅니다. 다른 모델과 비교할 때만 |
| `CHAT_LLM_REASONING_EFFORT`   | `medium`              | **인덱싱의 `low` 와 다릅니다** — 아래 참고                  |
| `CHAT_MAX_TOOL_ITERATIONS`    | `6`                   | 툴 왕복 상한. 도달하면 모은 근거로 답을 강제                |
| `CHAT_HISTORY_TURNS`          | `8`                   | 모델에 넘기는 최근 턴 수                                    |

- **채팅만 `medium` 인 이유**: 툴을 고르는 일은 생성이 아니라 **판정**입니다. Solar 는 판정에서
  `low` 면 오판합니다(실측). 반대로 색인 요약 같은 생성 계열은 `low` 로 충분하고 훨씬 빠릅니다.
- 서버 쪽에 `--enable-auto-tool-choice --tool-call-parser solar_open2` 가 켜져 있어야 합니다.
  꺼져 있으면 모델이 `search_wiki` 를 부르지 않아 근거 없는 답이 나옵니다.
- 응답은 즉답이 아닙니다 — 검색 한 번이면 3~4초, 툴이 여러 번 도는 후속 질문은 10초를 넘습니다.

### 백업 / 복원

PostgreSQL(pg_dump custom) + MinIO 버킷(mc mirror)을 `backups/{timestamp}/` 에 저장하고 최근
N개(기본 7)만 보존합니다.

```bash
scripts/backup.sh
scripts/restore.sh backups/20260718-120000 [--yes]
```

> cron 예(매일 03:00): `0 3 * * * cd /path/to/flex-drive && scripts/backup.sh >> /var/log/minidrive-backup.log 2>&1`

## 개발

```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000     # DB/MinIO/Redis 는 compose 로 띄워두고 실행

# frontend
cd frontend && npm install && npm run dev     # http://localhost:5173 (/api → :8000 프록시)
```

### 테스트

```bash
cd backend && pytest        # 유닛
```

통합 테스트(`backend/tests/integration_*.py`)는 **compose 스택이 뜬 상태에서 compose 네트워크 내부의
일회성 컨테이너**로 돌립니다. 런타임 이미지에는 dev 의존성이 없어 소스를 마운트하고 httpx 를 임시
설치합니다:

```bash
docker compose run --rm \
  -v "$(pwd)/backend/app:/app/app" -v "$(pwd)/backend/tests:/app/tests" \
  -v "$(pwd)/backend/alembic:/app/alembic" \
  -e RATE_LIMIT_ENABLED=false \
  --entrypoint sh backend -c "pip install -q httpx && python -m tests.integration_files"
```

- `integration_admin` 은 rate limit 자체를 검증하므로 끄지 않고 실행합니다. `integration_resumable`
  은 `-e RESUMABLE_PART_SIZE=5242880` 을 함께 줍니다.
- `alembic/` 도 마운트하세요. 테스트가 스키마를 재생성한 뒤 head 로 stamp 하므로, 이미지에 없는 새
  리비전이 있으면 `Can't locate revision` 으로 실패합니다.
- **통합 테스트는 파괴적입니다** — dev DB/버킷을 초기화합니다. 운영 데이터가 있는 곳에서 돌리지
  마세요.
- `DROP TABLE` 에서 멈추면 열려 있는 브라우저 탭을 닫아 보세요. SSE 스트림이 살아 있으면 DB 세션이
  `idle in transaction` 으로 남아 DDL 을 막습니다.

E2E:

```bash
cd frontend
npx playwright install --with-deps chromium   # 최초 1회
npx playwright test
```

> rate limit 은 `globalSetup` 이 실행 동안만 끄고 끝나면 원복합니다(`e2e/support/stack.ts`).
> 손으로 끄지 마세요 — 되돌리는 것을 잊으면 스택이 약해진 채 남습니다.

**모델이 필요한 스펙이 둘 있습니다.**

- `e2e/chat/ask-and-cite.spec.ts` — 실제 사내 vLLM 을 3회 호출합니다(질문·후속 질문·비교표).
  vLLM 에 닿지 않으면 이 스펙만 실패합니다. 실측 36~43초.
- `e2e/chat/session-crud.spec.ts` — 질문을 보내지 않으므로 **vLLM 없이 돕니다.**

백엔드 쪽 대화 축은 `backend/tests/integration_chat.py` 가 봅니다(역시 실제 vLLM 호출). 그래프
분기(왕복 상한·폴백·아티팩트 검증)는 모델 없이 `backend/tests/test_chat_agent_unit.py` 가 덮습니다.

## 디렉터리

```
backend/    FastAPI — app/{api,services,models,schemas,core}, alembic/, tests/
frontend/   React SPA — src/{api,components,lib,pages}, e2e/
nginx/      게이트웨이
deploy/     서브패스 배포 자산 (DEPLOY.md, compose, nginx snippet)
scripts/    backup.sh / restore.sh
spec/       PRD + 설계 문서 (wiki-index, permissions, trash-retention-purge …)
```

## Solar 모델 사용 후기

Solar-Open2 가 허깅페이스에 공개된 뒤 사내 vLLM 에 올려 위키와 채팅을 이 모델 하나로 만들었습니다.

**한국어 문서를 잘 읽습니다.** 위키 색인은 [PageIndex](https://github.com/VectifyAI/PageIndex)
방식으로 문서를 절 단위로 쪼개고, 절마다 요약을 붙이는 일을 모델에 맡깁니다. 사전 검증에서
PageIndex 에 이 모델을 물려 보니 25 KB 짜리 마크다운 하나가 8초 만에 트리가 됐고, JSON 파싱은 한
번도 실패하지 않았습니다. 요약도 한국어로 자연스럽게 나왔습니다. 사람이 절을 나누듯 구조를 잡아
주니 답변에 파일·절·줄 번호까지 근거로 달 수 있습니다.

**에이전트가 잘 동작합니다.** 툴을 알아서 고르고, 앞선 대화를 검색어로 다시 쓰고, 답을 표로 낼지
평문으로 낼지도 검색 결과를 보고 정합니다. 
