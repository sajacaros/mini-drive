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

현재 저장소는 **Phase 1 스캐폴딩** 단계입니다.
