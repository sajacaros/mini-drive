# Mini Drive 서버 배포 가이드 (기존 nginx 뒤 서브패스 `/drive`)

이미 80/443(TLS)를 처리하는 **기존 host nginx** 뒤에 `https://<host>/drive` 로 붙이는 구성.
배포 호스트에 SSH 로 접속해 **거기서 이미지를 빌드하고** compose 로 띄운다(레지스트리 없음).
평소엔 **수동 배포**(1장)로 충분하고, 원하면 **Jenkins 로 자동화**(2장)할 수 있다.

```
(수동) SSH 접속 → 호스트: git pull → docker compose build → up -d
브라우저 → 기존 nginx(443/TLS) → /drive/ 프록시 → flexdrive-gateway(:7755) → backend/frontend/...
```

**핵심 설계 — 경로를 이미지에 굽지 않는다.** 프론트 이미지는 Vite `base=/__BASE__/` 플레이스홀더로
빌드돼 경로에 무관하고, 컨테이너 시작 시 `BASE_PATH` 환경변수(기본 `/`)로 치환된다. 그래서
같은 이미지가 로컬(`/`)에서도, 서버(`/drive/`)에서도 **재빌드 없이** 동작한다.

---

## 0. 서버 준비 (1회)

### 0-1. 배포 계정 생성 (관리자 sudo 로)
빌드·compose 를 실행할 전용 계정 `drive-deployer` 를 만든다.
```bash
sudo useradd -m -s /bin/bash drive-deployer
sudo passwd drive-deployer                       # 로그인 비밀번호 설정
sudo usermod -aG docker drive-deployer           # sudo 없이 docker/compose 실행 (새 세션부터 적용)

sudo mkdir -p /var/local/flex-drive
sudo chown -R drive-deployer:drive-deployer /var/local/flex-drive
```
> 🔐 비밀번호 로그인은 간단하지만, 서버가 인터넷에 노출돼 있으면 무차별 대입 위험이 있다.
> 공개 서버이거나 Jenkins 자동화를 붙일 거면 **SSH 키 로그인**을 권장한다(2-1 참고).

### 0-2. 리포 + 시크릿 (`drive-deployer` 로 로그인해서)
PuTTY 등으로 `drive-deployer` 계정에 접속한 뒤:
```bash
git clone <repo-url> /var/local/flex-drive
cd /var/local/flex-drive/deploy
cp .env.deploy.example .env && chmod 600 .env
# BASE_PATH=/drive/ 확인하고, 아래 셋은 강한 랜덤값으로 교체:
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 24   # MINIO_ROOT_PASSWORD
```

### 0-3. 스택 기동
```bash
cd /var/local/flex-drive/deploy
docker compose -f docker-compose.deploy.yml build   # backend/frontend 를 호스트에서 빌드
docker compose -f docker-compose.deploy.yml up -d
docker compose -f docker-compose.deploy.yml ps
```
게이트웨이가 `127.0.0.1:7755` 에서 대기한다. 확인:
```bash
curl -fsS http://127.0.0.1:7755/health && echo OK
```

### 0-4. 기존 nginx 에 프록시 추가
`deploy/nginx-snippet.conf` 의 location 블록을 기존 nginx 의 **443 server 블록 안**에 넣고 reload.
`proxy_pass` 대상은 아래 "기존 nginx 연결" 절 참고.
```bash
# 기존 nginx 가 호스트에 있으면:
sudo nginx -t && sudo systemctl reload nginx
# 기존 nginx 가 컨테이너면:
docker exec <기존nginx컨테이너> nginx -t && docker exec <기존nginx컨테이너> nginx -s reload
```

### 0-5. 첫 관리자 셋업
브라우저에서 `https://<host>/drive/setup` → 셋업 위저드로 첫 관리자 생성.
(비상 복구: `docker compose -f docker-compose.deploy.yml exec backend python -m app.cli create-admin --email you@example.com`)

---

## 기존 nginx 연결 — proxy_pass 대상 결정

우리 게이트웨이(`flexdrive-gateway`)에 기존 nginx 가 어떻게 닿느냐가 핵심.

### (A) 기존 nginx 가 호스트 프로세스거나 host 네트워크
그대로 `proxy_pass http://127.0.0.1:7755/;` (스니펫 기본값). 끝.

### (B) 기존 nginx 가 브리지 네트워크의 컨테이너  ← 흔함
컨테이너는 호스트의 `127.0.0.1:7755`(loopback)에 **닿지 못한다.** 두 컨테이너를 같은
도커 네트워크에 붙이고 컨테이너명으로 프록시하는 게 가장 깔끔하고 안전하다:

1. `docker-compose.deploy.yml` 의 `nginx.ports` (`127.0.0.1:7755:80`) 를 **삭제**(포트 노출 불필요).
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

## 1. 배포 / 재배포 (수동)

코드가 바뀔 때마다 `drive-deployer` 로 접속해 아래를 실행한다:
```bash
cd /var/local/flex-drive
git pull --ff-only                    # 최신 코드
cd deploy
DC="docker compose -f docker-compose.deploy.yml"
$DC build                             # 바뀐 소스로 재빌드
$DC up -d                             # 변경된 컨테이너만 재생성
docker image prune -f                 # dangling 이미지 정리
curl -fsS http://127.0.0.1:7755/health && echo OK
```
> 프론트는 경로 무관하게 빌드되므로, 배포 경로를 바꾸려면 `.env` 의 `BASE_PATH` 만 바꾸고 재기동하면 된다.

> **게이트웨이 재시작 불필요.** 게이트웨이 nginx 는 upstream(backend/frontend/minio) 을
> 도커 내장 DNS(`127.0.0.11`)로 **요청 시점에 다시 해석**한다(`nginx/conf.d/default.conf` 의
> `resolver` + 변수 `proxy_pass`). 그래서 `$DC up -d` 가 backend/frontend 만 새 IP 로 재생성해도
> 게이트웨이가 옛 IP 를 물지 않는다. (예전엔 nginx 가 시작 시 IP 를 한 번만 캐시해,
> 재빌드 때마다 `connect() failed (113: Host is unreachable)` 502 가 나 게이트웨이 강제
> 재생성이 필요했다.) 만약 그래도 502 가 나면 `$DC logs nginx` 로 upstream 주소를 확인한다.

---

## 2. (선택) Jenkins 자동화

`main` push → 자동 배포까지 원하면 Jenkins 를 붙인다. 빌드는 호스트에서 하므로
Jenkins 는 **SSH 만** 하면 되고, docker CLI·소켓 마운트·Docker Hub 계정이 **필요 없다.**
단, Jenkins 자동화는 비밀번호가 아니라 **SSH 키**가 필요하다.

### 2-1. `drive-deployer` 에 SSH 키 등록
접속할 클라이언트(Jenkins)에서 키쌍을 만들고, 공개키만 호스트에 등록한다.
```bash
# (클라이언트에서) 키쌍 생성 — 개인키는 Jenkins Credentials 로, 공개키는 아래로
ssh-keygen -t ed25519 -C jenkins-deploy -f ./jenkins_deploy_key -N ''

# (호스트에서) 공개키를 restrict 붙여 등록 — 포트포워딩·PTY 차단(명령 실행엔 지장 없음)
sudo -u drive-deployer mkdir -p /home/drive-deployer/.ssh
sudo -u drive-deployer chmod 700 /home/drive-deployer/.ssh
echo 'restrict ssh-ed25519 AAAA...실제공개키... jenkins-deploy' \
  | sudo -u drive-deployer tee -a /home/drive-deployer/.ssh/authorized_keys
sudo -u drive-deployer chmod 600 /home/drive-deployer/.ssh/authorized_keys
```
> 검증: `ssh -i jenkins_deploy_key drive-deployer@<호스트IP> 'whoami && docker ps'` → `drive-deployer` + 권한 에러 없이 컨테이너 목록.
> ⚠️ `docker` 그룹은 실질 root 권한이니 개인키 관리에 유의.

### 2-2. 플러그인 + 자격증명 (Manage Jenkins → Credentials)
- 플러그인: **SSH Agent** (`sshagent` 스텝용).

| ID | 종류 | 내용 |
|----|------|------|
| `deploy-ssh` | SSH Username with private key | `drive-deployer` 개인키(위 `jenkins_deploy_key`) |

### 2-3. Jenkinsfile 값 수정
리포 루트 `Jenkinsfile` 상단 `environment`:
- `DEPLOY_HOST` → `drive-deployer@<호스트IP>`

### 2-4. 파이프라인 잡 + 웹훅
New Item → Pipeline → "Pipeline script from SCM" → 이 리포 → `Jenkinsfile`. GitHub 웹훅 연결.

### 자동 배포 흐름
`main` push → Jenkins 가 SSH 로 호스트에 접속해:
1. `git fetch` + `git reset --hard origin/main` (origin 과 정확히 일치)
2. `docker compose -f docker-compose.deploy.yml build` (호스트에서 backend/frontend 빌드)
3. `up -d` → dangling 이미지 prune
4. backend 컨테이너가 기동 시 `alembic upgrade head` 자동 실행 (마이그레이션 스텝 불필요)
5. `http://127.0.0.1:7755/health` 헬스체크(20회×3초 재시도)

---

## 3. 운영

```bash
cd /var/local/flex-drive/deploy
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
호스트 빌드이므로 소스를 되돌리고 재빌드한다:
```bash
cd /var/local/flex-drive
git reset --hard <이전-git-sha>          # 되돌릴 커밋
cd deploy
docker compose -f docker-compose.deploy.yml build backend frontend
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
