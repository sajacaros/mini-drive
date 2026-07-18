#!/bin/sh
# 컨테이너 기동 시 DB 마이그레이션을 선행 적용한 뒤 앱을 실행한다.
# db 헬스체크(compose)로 준비를 보장하지만, 재시작 레이스 대비로 짧게 재시도한다.
set -e

echo "[entrypoint] alembic upgrade head ..."
tries=0
until alembic upgrade head; do
    tries=$((tries + 1))
    if [ "$tries" -ge 10 ]; then
        echo "[entrypoint] 마이그레이션 실패 — 10회 재시도 초과. 종료." >&2
        exit 1
    fi
    echo "[entrypoint] DB 준비 대기 중... (${tries}/10)"
    sleep 3
done
echo "[entrypoint] 마이그레이션 완료."

exec "$@"
