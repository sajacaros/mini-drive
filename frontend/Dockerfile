# ── build stage ───────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

# ── serve stage ───────────────────────────────────────
FROM nginx:1.27-alpine

# SPA 라우팅용 설정. docker-compose 에서 동일 파일을 bind-mount 로도 덮어쓸 수 있다.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
