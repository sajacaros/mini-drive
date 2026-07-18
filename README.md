# Mini Drive

사내 구성원 대상 **경량 구글 드라이브** — 파일 업로드·다운로드·버전 관리·공유 URL 생성·그룹 기반 권한 관리를 웹으로 제공하며, 운영 전체를 Docker 로 재현 가능하게 구성한 사내 자가 호스팅 파일 공유/관리 서비스입니다.

전체 요구사항은 [`spec/minidrive-prd.md`](spec/minidrive-prd.md) 를 진실 소스로 삼습니다.

## 아키텍처 개요

```
브라우저 ─▶ nginx(게이트웨이, :80) ─┬─▶ /        → frontend (React SPA)
                                    ├─▶ /api/    → backend (FastAPI)
                                    └─▶ /_minio/ → MinIO (X-Accel-Redirect 내부 전용)
                                                    backend ─▶ PostgreSQL / Redis / MinIO
```

- **게이트웨이 다운로드 모델**: 브라우저에 presigned URL 을 직접 발급하지 않고, FastAPI 가 매 요청 인가 후 `X-Accel-Redirect` 로 nginx→MinIO 스트리밍합니다. 공유 링크 비활성화 시 즉시 차단됩니다.
- **MinIO 익명 접근 완전 차단**(`mc anonymous set none`): 게이트웨이 모델의 전제 조건입니다.
- **backend 는 호스트 포트를 노출하지 않습니다.** 모든 트래픽은 nginx 게이트웨이를 경유합니다.

## 기술 스택

| 계층 | 기술 |
|---|---|
| Frontend | React 19, TypeScript 5, Tailwind CSS 4, React Router 7, Zustand 5, Axios, Vite |
| Backend | Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 (async/asyncpg), Alembic, PyJWT, argon2 |
| Storage | MinIO (S3 호환) |
| Database | PostgreSQL 16 |
| Cache/Token | Redis 7 |
| Gateway | nginx 1.27 |

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

> nginx 설정을 변경한 경우 inode 교체 문제를 피하려면 컨테이너를 재생성하세요:
> `docker compose up -d --force-recreate nginx`

## 운영 (프로덕션)

프로덕션은 base compose 에 `docker-compose.prod.yml` 오버라이드를 얹어 기동합니다.
오버라이드는 `restart: unless-stopped`, JSON 구조화 로깅(`LOG_FORMAT=json`), 도커 로그 로테이션
(`json-file` max-size/max-file), 리소스 limits, `DEBUG=false` 를 적용합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
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
`rate_limit_rejections_total{scope}`. `path` 라벨은 실제 경로가 아닌 **라우트 템플릿**입니다.

`/metrics` 는 **게이트웨이(nginx)에서 외부 접근을 차단**(`deny all` → 403)하며, 내부 도커
네트워크의 스크레이퍼만 `backend:8000/metrics` 로 접근합니다. Prometheus 스크레이프 예:

```yaml
scrape_configs:
  - job_name: minidrive-backend
    metrics_path: /metrics
    static_configs:
      - targets: ["backend:8000"]   # minidrive 네트워크 내부에서 실행 시
```

```bash
# 내부에서 스크레이프 확인
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode()[:500])"

# 외부(게이트웨이)에서는 차단됨 → 403
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/metrics
```

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

> cron 등록 예(매일 03:00): `0 3 * * * cd /path/to/mini-drive && scripts/backup.sh >> /var/log/minidrive-backup.log 2>&1`

### TLS (443)

`docker-compose.prod.yml` 의 nginx 서비스에 인증서 마운트 지점과 자가서명 예시 명령이
주석으로 안내되어 있습니다. 인증서를 `nginx/certs/` 에 배치하고 볼륨 주석을 해제한 뒤,
`nginx/conf.d` 에 443 server 블록을 추가하세요.

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

## 디렉터리 구조

```
mini-drive/
├── backend/                 # FastAPI 백엔드
│   ├── app/
│   │   ├── main.py          # 앱 팩토리 + /health
│   │   ├── core/            # config, database, redis
│   │   └── api/router.py    # 최상위 라우터 (도메인 라우터 include 지점)
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                # React SPA
│   ├── src/
│   │   ├── main.tsx / App.tsx
│   │   ├── api/client.ts    # axios 인스턴스 (baseURL=/api)
│   │   └── pages/
│   ├── package.json
│   ├── Dockerfile           # multi-stage: node build → nginx serve
│   └── nginx.conf
├── nginx/                   # 게이트웨이
│   ├── nginx.conf
│   └── conf.d/default.conf
├── spec/minidrive-prd.md    # PRD (진실 소스)
├── docker-compose.yml
└── .env.example
```

## 개발 마일스톤 (PRD 9절)

| Phase | 내용 |
|---|---|
| **1. MVP** | 인증(가입 승인제) + 파일 업로드/다운로드/목록 + 공유 링크 + 최소 admin |
| **2. 버전 관리** | 버전 히스토리 / 복구 / 충돌 감지 |
| **3. 그룹 & 권한** | 그룹 CRUD, 폴더 권한 상속(조회 시 판정 + Redis 캐시) |
| **4. 운영 안정성 + Admin** | 게이트웨이 정제, 메트릭, rate limiting, admin 대시보드 |
| **5. 고도화** | 재개 업로드, 썸네일, 미리보기, UI 테마 4종, 델타 동기화 |

Phase 1~3(인증·파일·버전·그룹/권한)에 이어 **Phase 4 운영 안정성**(구조화 로깅, Prometheus 메트릭,
rate limiting, 백업/복원 스크립트, 프로덕션 compose 프로파일)까지 반영되어 있습니다.
