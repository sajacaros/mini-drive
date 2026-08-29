import { cpSync, existsSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

/**
 * pdf.js 보조 자원을 public/ 아래로 복사한다.
 *
 * PDF 를 canvas 로 직접 그리려면(components/PdfViewer.tsx) 두 가지가 런타임에 필요하다:
 *   - cmaps/         — CID 폰트의 예약 CMap(UniKS-UCS2-H 등). 한글 PDF 가 폰트를 임베드하지
 *                      않고 예약 인코딩을 쓰면 이게 없을 때 글자가 통째로 깨진다.
 *   - standard_fonts/— Helvetica 류 표준 14 폰트. 역시 임베드가 없을 때 대체된다.
 * 둘 다 파일 하나를 열 때 필요한 것만 가져가므로 번들에 들어가지 않고, 자체 호스팅
 * 배포에서 외부 CDN 에 기대지 않기 위해 우리가 같이 실어 나른다.
 *
 * public/ 산출물이라 dev 서버와 빌드(dist) 양쪽에서 같은 경로로 서빙된다.
 */
function pdfjsAssets(): Plugin {
  return {
    name: "minidrive:pdfjs-assets",
    buildStart() {
      for (const dir of ["cmaps", "standard_fonts"]) {
        const src = here(`node_modules/pdfjs-dist/${dir}`);
        if (!existsSync(src)) continue;
        cpSync(src, here(`public/pdfjs/${dir}`), { recursive: true });
      }
    },
  };
}

export default defineConfig({
  // 서브패스 배포용 베이스 경로. 로컬 개발/기본은 "/".
  // Docker 이미지 빌드 시엔 VITE_BASE=/__BASE__/ 플레이스홀더로 굽고, 컨테이너 시작 시
  // BASE_PATH 값으로 치환한다(docker-entrypoint.d/40-base-path.sh) → 재빌드 없이 경로 전환.
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss(), pdfjsAssets()],
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
