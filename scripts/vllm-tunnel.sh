#!/usr/bin/env bash
# 사내 vLLM(GLM 5.2) SSH 터널 — 로컬 0.0.0.0:17900 → 원격 localhost:7900.
#
# 접속 정보는 저장소 루트의 `env` 파일(gitignore, 커밋 금지)에서 읽는다:
#   sshUrl=<host> / sshPort=<port> / sshId=<user>
# 인증은 SSH 키(~/.ssh/id_ed25519, 원격에 등록됨)를 쓴다 — 비밀번호는 읽지 않는다.
# 컨테이너(backend/worker)는 http://host.docker.internal:17900/v1 로 접근한다(.env 참조).

set -euo pipefail
cd "$(dirname "$0")/.."

LOCAL_PORT="${LOCAL_PORT:-17900}"
REMOTE_PORT="${REMOTE_PORT:-7900}"

sshUrl=$(grep -oP '^sshUrl=\K.*' env)
sshPort=$(grep -oP '^sshPort=\K.*' env)
sshId=$(grep -oP '^sshId=\K.*' env)

# 이미 떠 있으면 재사용.
if curl -sf -m 3 -o /dev/null "http://127.0.0.1:${LOCAL_PORT}/version" 2>/dev/null; then
  echo "터널 이미 동작 중 (127.0.0.1:${LOCAL_PORT})"
  exit 0
fi

ssh -f -N \
  -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "0.0.0.0:${LOCAL_PORT}:localhost:${REMOTE_PORT}" \
  -p "${sshPort}" "${sshId}@${sshUrl}"
echo "터널 연결됨: 0.0.0.0:${LOCAL_PORT} → ${sshUrl}:${REMOTE_PORT}"
