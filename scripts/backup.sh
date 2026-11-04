#!/usr/bin/env bash
#
# Mini Drive 백업 스크립트 (PRD 12장 — 정기 백업).
#
# 대상: 실행 중인 docker compose 스택.
#   1) PostgreSQL  → pg_dump custom format (postgres.dump)
#   2) 오브젝트 버킷(SeaweedFS S3) → rclone sync (objects/ 디렉터리)
# 산출물: backups/{timestamp}/  (+ manifest.txt)
# 보존:   최근 N개(기본 7)만 유지, 나머지 삭제.
#
# 사용법:
#   scripts/backup.sh                 # 기본 보존 7개
#   BACKUP_RETENTION=14 scripts/backup.sh
#
# 자격 증명은 프로젝트 루트 .env 에서 읽는다(없으면 compose 기본값).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# ── 설정 로드 ──────────────────────────────────────────
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT_DIR/.env"
    set +a
fi

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-minidrive}"
MINIO_BUCKET="${MINIO_BUCKET:-minidrive}"
RETENTION="${BACKUP_RETENTION:-7}"

DC="docker compose"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT_DIR/backups/$TIMESTAMP"

log() { printf '[backup] %s\n' "$*"; }
fail() { printf '[backup][ERROR] %s\n' "$*" >&2; exit 1; }

# ── 사전 점검: 스택 기동 여부 ──────────────────────────
if ! $DC ps --status running --services 2>/dev/null | grep -qx db; then
    fail "db 컨테이너가 실행 중이 아닙니다. 'docker compose up -d' 후 다시 실행하세요."
fi

mkdir -p "$BACKUP_DIR/objects"
log "백업 시작 → $BACKUP_DIR"

# ── 1) PostgreSQL (custom format) ─────────────────────
log "PostgreSQL 덤프(pg_dump -F c) ..."
$DC exec -T db pg_dump -U "$POSTGRES_USER" -F c "$POSTGRES_DB" > "$BACKUP_DIR/postgres.dump"
DB_SIZE="$(du -h "$BACKUP_DIR/postgres.dump" | cut -f1)"
log "PostgreSQL 덤프 완료 ($DB_SIZE)"

# ── 2) 오브젝트 버킷 미러 ─────────────────────────────
# compose 의 rclone 도구 서비스(profiles: tools)를 스택 네트워크에 붙여 버킷을 호스트 마운트로
# 미러한다. --user 로 산출물을 호스트 사용자 소유로 만들어 보존 정리(rm)가 가능하게 한다.
# 자격 증명은 compose 가 .env 의 MINIO_ROOT_USER/PASSWORD 로 rclone 환경에 넣는다.
log "오브젝트 버킷 미러(rclone sync s3:$MINIO_BUCKET) ..."
$DC --profile tools run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    -v "$BACKUP_DIR/objects:/backup" \
    rclone sync "s3:$MINIO_BUCKET" /backup
OBJ_SIZE="$(du -sh "$BACKUP_DIR/objects" | cut -f1)"
log "오브젝트 미러 완료 ($OBJ_SIZE)"

# ── manifest ──────────────────────────────────────────
cat > "$BACKUP_DIR/manifest.txt" <<EOF
mini-drive backup
timestamp:   $TIMESTAMP
created_at:  $(date -Iseconds)
postgres_db: $POSTGRES_DB (pg_dump custom format → postgres.dump)
bucket:      $MINIO_BUCKET (rclone sync → objects/)
db_size:     $DB_SIZE
objects_size:$OBJ_SIZE
EOF

# ── 보존 정리: 최근 RETENTION 개만 유지 ───────────────
log "보존 정리: 최근 $RETENTION 개 유지 ..."
mapfile -t OLD < <(ls -1d "$ROOT_DIR"/backups/*/ 2>/dev/null | sort | head -n -"$RETENTION" || true)
for dir in "${OLD[@]:-}"; do
    [ -n "$dir" ] || continue
    log "  오래된 백업 삭제: $dir"
    rm -rf "$dir"
done

log "백업 완료: $BACKUP_DIR"
