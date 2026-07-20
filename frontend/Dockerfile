# ── build stage ───────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
# 경로에 무관한 이미지를 만들기 위해 base 를 플레이스홀더로 굽는다.
# 실제 경로는 컨테이너 시작 시 BASE_PATH 로 치환된다(docker-entrypoint.d/40-base-path.sh).
ENV VITE_BASE=/__BASE__/
RUN npm run build

# ── serve stage ───────────────────────────────────────
FROM nginx:1.27-alpine

# SPA 라우팅용 설정. docker-compose 에서 동일 파일을 bind-mount 로도 덮어쓸 수 있다.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# 런타임 베이스 경로 주입 스크립트 (nginx 기동 전 자동 실행).
COPY docker-entrypoint.d/40-base-path.sh /docker-entrypoint.d/40-base-path.sh
RUN chmod +x /docker-entrypoint.d/40-base-path.sh

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
