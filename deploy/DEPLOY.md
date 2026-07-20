# Mini Drive 서버 배포 가이드 (기존 nginx 뒤 서브패스 `/drive`)

이미 80/443(TLS)를 처리하는 **기존 host nginx** 뒤에 `https://<host>/drive` 로 붙이는 구성.
CI 는 Docker Hub 로 이미지를 빌드·푸시하고, 배포는 SSH 로 호스트에서 compose 를 실행한다.

```
GitHub push → Jenkins(build+push to Docker Hub) → SSH → 호스트에서 docker compose pull & up
브라우저 → 기존 nginx(443/TLS) → /drive/ 프록시 → flexdrive-gateway(:80) → backend/frontend/...
```

**핵심 설계 — 경로를 이미지에 굽지 않는다.** 프론트 이미지는 Vite `base=/__BASE__/` 플레이스홀더로
빌드돼 경로에 무관하고, 컨테이너 시작 시 `BASE_PATH` 환경변수(기본 `/`)로 치환된다. 그래서
같은 이미지가 로컬(`/`)에서도, 서버(`/drive/`)에서도 **재빌드 없이** 동작한다.

---

## 0. 서버 준비 (1회)

### 0-1. 리포 체크아웃 + 시크릿
설정 파일(compose·nginx)만 필요하다. 앱은 이미지로 온다.
```bash
sudo mkdir -p /opt/flex-drive && sudo chown "$USER" /opt/flex-drive
git clone <repo-url> /opt/flex-drive
cd /opt/flex-drive/deploy
cp .env.deploy.example .env && chmod 600 .env
# BASE_PATH=/drive/, DOCKERHUB_USER 채우고, 아래 셋은 강한 랜덤값으로 교체:
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 24   # MINIO_ROOT_PASSWORD
```

### 0-2. 스택 기동
```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
docker compose -f docker-compose.deploy.yml ps
```
게이트웨이가 `127.0.0.1:8080` 에서 대기한다. 확인:
```bash
curl -fsS http://127.0.0.1:8080/health && echo OK
```

### 0-3. 기존 nginx 에 프록시 추가
`deploy/nginx-snippet.conf` 의 location 블록을 기존 nginx 의 **443 server 블록 안**에 넣고 reload.
`proxy_pass` 대상은 아래 "기존 nginx 연결" 절 참고.
```bash
# 기존 nginx 가 호스트에 있으면:
sudo nginx -t && sudo systemctl reload nginx
# 기존 nginx 가 컨테이너면:
docker exec <기존nginx컨테이너> nginx -t && docker exec <기존nginx컨테이너> nginx -s reload
```

### 0-4. 첫 관리자 셋업
브라우저에서 `https://<host>/drive/setup` → 셋업 위저드로 첫 관리자 생성.
(비상 복구: `docker compose -f docker-compose.deploy.yml exec backend python -m app.cli create-admin --email you@example.com`)

---

## 기존 nginx 연결 — proxy_pass 대상 결정

우리 게이트웨이(`flexdrive-gateway`)에 기존 nginx 가 어떻게 닿느냐가 핵심.

### (A) 기존 nginx 가 호스트 프로세스거나 host 네트워크
그대로 `proxy_pass http://127.0.0.1:8080/;` (스니펫 기본값). 끝.

### (B) 기존 nginx 가 브리지 네트워크의 컨테이너  ← 흔함
컨테이너는 호스트의 `127.0.0.1:8080`(loopback)에 **닿지 못한다.** 두 컨테이너를 같은
도커 네트워크에 붙이고 컨테이너명으로 프록시하는 게 가장 깔끔하고 안전하다:

1. `docker-compose.deploy.yml` 의 `nginx.ports` (`127.0.0.1:8080:80`) 를 **삭제**(포트 노출 불필요).
2. 기존 nginx 가 속한 외부 네트워크를 우리 게이트웨이에도 붙인다. 예: 기존 네트워크명이 `web` 이면
   `docker-compose.deploy.yml` 하단에 추가:
   ```yaml
   networks:
     minidrive:
       driver: bridge
     web:
       external: true
   ```
   그리고 `nginx` 서비스의 `networks:` 에 `web` 추가:
   ```yaml
   services:
     nginx:
       networks:
         - minidrive
         - web
   ```
3. 스니펫의 proxy_pass 를 컨테이너명으로:
   ```nginx
   proxy_pass http://flexdrive-gateway:80/;
   ```
4. `docker compose -f docker-compose.deploy.yml up -d` 후 기존 nginx reload.

> 기존 nginx 의 네트워크명은 `docker inspect <기존nginx컨테이너> -f '{{json .NetworkSettings.Networks}}'` 로 확인.

---

## 1. Jenkins 설정 (1회)

### 1-1. Jenkins 컨테이너의 docker 접근
빌드하려면 Jenkins 컨테이너에 **docker CLI + 호스트 소켓 마운트**가 필요:
`-v /var/run/docker.sock:/var/run/docker.sock`.

### 1-2. 자격증명 (Manage Jenkins → Credentials)
| ID | 종류 | 내용 |
|----|------|------|
| `dockerhub` | Username with password | Docker Hub 계정 (push 권한) |
| `deploy-ssh` | SSH Username with private key | 호스트 배포 계정 개인키 |

호스트 배포 계정:
```bash
sudo useradd -m -s /bin/bash deploy && sudo usermod -aG docker deploy
sudo -u deploy mkdir -p /home/deploy/.ssh   # 공개키를 authorized_keys 에 (chmod 600)
sudo chown -R deploy /opt/flex-drive
```

### 1-3. Jenkinsfile 값 수정
리포 루트 `Jenkinsfile` 상단 `environment`:
- `DOCKERHUB_USER` → 실제 Docker Hub 계정
- `DEPLOY_HOST`    → `deploy@<호스트IP>`

### 1-4. 파이프라인 잡 + 웹훅
New Item → Pipeline → "Pipeline script from SCM" → 이 리포 → `Jenkinsfile`. GitHub 웹훅 연결.

---

## 2. 이후 배포 흐름 (자동)

`main` push → Jenkins 가:
1. 커밋 SHA 태그로 backend/frontend 이미지 빌드 → Docker Hub push (`:sha`, `:latest`)
2. SSH 로 호스트 → `git pull` → `compose pull` → `up -d`
3. backend 컨테이너가 기동 시 `alembic upgrade head` 자동 실행 (마이그레이션 스텝 불필요)
4. `http://127.0.0.1:8080/health` 스모크 테스트

프론트 이미지는 경로 무관하므로, 서버 `.env` 의 `BASE_PATH` 만 바꾸면 배포 경로가 바뀐다(재빌드 X).

---

## 3. 운영

```bash
cd /opt/flex-drive/deploy
DC="docker compose -f docker-compose.deploy.yml"
$DC ps                # 상태
$DC logs -f backend   # 로그(JSON)
$DC restart backend
```

### 백업
```bash
$DC exec -T db pg_dump -U postgres -F c minidrive > backup.dump
# MinIO 는 scripts/backup.sh 참고(compose 파일 경로만 맞춰 조정)
```

### 롤백
```bash
cd /opt/flex-drive/deploy
export IMAGE_TAG=<이전-git-sha>
docker compose -f docker-compose.deploy.yml up -d backend frontend
```

---

## 부록 — 프론트 서브패스 동작 원리

| 지점 | 처리 |
|------|------|
| 에셋 URL (`/drive/assets/...`) | Vite `base=/__BASE__/` → 시작 시 `/drive/` 치환 |
| API (`axios`, 무헤더 fetch, SSE, 다운로드 URL) | `withBase()` 가 런타임 베이스 접두 (`src/lib/basePath.ts`) |
| 라우팅 | `<BrowserRouter basename={import.meta.env.BASE_URL}>` |
| 공유 링크 | `${origin}${withBase('/s/...')}` |
| 접두어 제거 | 기존 nginx `proxy_pass .../;` 끝 슬래시가 `/drive` 를 벗겨 우리 스택엔 루트로 도착 |
