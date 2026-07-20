#!/bin/sh
# 서브패스 배포용 런타임 베이스 경로 주입.
#
# 이미지는 Vite base=/__BASE__/ 로 구워져 경로에 무관하다(플레이스홀더). 컨테이너가 뜰 때
# 환경변수 BASE_PATH(기본 "/") 값으로 정적 파일 안의 /__BASE__/ 를 일괄 치환한다.
# nginx 공식 이미지가 /docker-entrypoint.d/*.sh 를 실행한 뒤 nginx 를 기동하므로 별도 설정 불필요.
#
#   BASE_PATH=/          → 로컬/기본 (루트에서 서빙)
#   BASE_PATH=/drive/    → https://host/drive 서브패스
set -eu

BASE_PATH="${BASE_PATH:-/}"
# 반드시 "/" 로 끝나야 에셋 상대 계산이 맞는다.
case "$BASE_PATH" in
  */) ;;
  *) BASE_PATH="${BASE_PATH}/" ;;
esac

echo "[base-path] BASE_PATH=${BASE_PATH} — '/__BASE__/' 치환 중..."
find /usr/share/nginx/html -type f \
  \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.map' \) \
  -exec sed -i "s#/__BASE__/#${BASE_PATH}#g" {} +
echo "[base-path] 완료."
