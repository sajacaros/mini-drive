#!/usr/bin/env bash
#
# Mini Drive 복원 스크립트 (PRD 12장).
#
# 지정한 백업 디렉터리를 실행 중인 docker compose 스택에 복원한다.
#   1) PostgreSQL  → pg_restore --clean --if-exists (기존 스키마 대체)
#   2) 오브젝트 버킷 → rclone sync (백업 상태로 일치 — 백업에 없는 객체는 지운다)
#
# 사용법:
#   scripts/restore.sh backups/20260718-120000
#   scripts/restore.sh backups/20260718-120000 --yes   # 확인 프롬프트 생략
#
# 주의: 이 스크립트는 현재 스택의 DB/버킷 데이터를 백업 시점으로 되돌립니다(파괴적).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

BACKUP_ARG="${1:-}"
ASSUME_YES="no"
[ "${2:-}" = "--yes" ] && ASSUME_YES="yes"

log() { printf '[restore] %s\n' "$*"; }
fail() { printf '[restore][ERROR] %s\n' "$*" >&2; exit 1; }

[ -n "$BACKUP_ARG" ] || fail "복원할 백업 디렉터리를 지정하세요. 예) scripts/restore.sh backups/20260718-120000"

# 상대/절대 경로 모두 허용.
BACKUP_DIR="$BACKUP_ARG"
[ -d "$BACKUP_DIR" ] || BACKUP_DIR="$ROOT_DIR/$BACKUP_ARG"
[ -d "$BACKUP_DIR" ] || fail "백업 디렉터리를 찾을 수 없습니다: $BACKUP_ARG"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

[ -f "$BACKUP_DIR/postgres.dump" ] || fail "postgres.dump 가 없습니다: $BACKUP_DIR"
# objects/ 가 현행 이름이다. MinIO 시절(mc mirror) 백업은 minio/ 에 들어 있다.
OBJ_DIR="$BACKUP_DIR/objects"
[ -d "$OBJ_DIR" ] || OBJ_DIR="$BACKUP_DIR/minio"
[ -d "$OBJ_DIR" ] || fail "objects/ (또는 구 minio/) 디렉터리가 없습니다: $BACKUP_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT_DIR/.env"
    set +a
fi
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-minidrive}"
S3_BUCKET="${S3_BUCKET:-minidrive}"

DC="docker compose"

if ! $DC ps --status running --services 2>/dev/null | grep -qx db; then
    fail "db 컨테이너가 실행 중이 아닙니다. 'docker compose up -d' 후 다시 실행하세요."
fi

# ── 확인 프롬프트 ─────────────────────────────────────
if [ "$ASSUME_YES" != "yes" ]; then
    printf '\n'
    printf '  다음 백업으로 현재 스택을 복원합니다(기존 데이터 대체):\n'
    printf '    백업:   %s\n' "$BACKUP_DIR"
    printf '    DB:     %s\n' "$POSTGRES_DB"
    printf '    버킷:   %s\n' "$S3_BUCKET"
    printf '\n'
    printf '  계속하려면 "yes" 를 입력하세요: '
    read -r reply
    [ "$reply" = "yes" ] || fail "사용자 취소."
fi

# ── 1) PostgreSQL 복원 ────────────────────────────────
log "PostgreSQL 복원(pg_restore --clean --if-exists) ..."
$DC exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner \
    < "$BACKUP_DIR/postgres.dump"
log "PostgreSQL 복원 완료."

# ── 2) 오브젝트 버킷 복원 ─────────────────────────────
# 버킷은 objectStorage-init 이 스택 기동 때 만들어 둔다.
log "오브젝트 버킷 복원(rclone sync → s3:$S3_BUCKET) ..."
$DC --profile tools run --rm --no-deps -T \
    -v "$OBJ_DIR:/backup:ro" \
    rclone sync /backup "s3:$S3_BUCKET"
log "오브젝트 버킷 복원 완료."

log "복원 완료: $BACKUP_DIR"
