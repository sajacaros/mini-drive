import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // 서브패스 배포용 베이스 경로. 로컬 개발/기본은 "/".
  // Docker 이미지 빌드 시엔 VITE_BASE=/__BASE__/ 플레이스홀더로 굽고, 컨테이너 시작 시
  // BASE_PATH 값으로 치환한다(docker-entrypoint.d/40-base-path.sh) → 재빌드 없이 경로 전환.
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
    // 로컬 개발 시 게이트웨이(nginx) 대신 backend 로 직접 프록시
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
