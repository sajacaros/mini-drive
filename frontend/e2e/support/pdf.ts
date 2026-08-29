import type { Page } from "@playwright/test";

/**
 * E2E 용 PDF 시드.
 *
 * 저장소에 이진 픽스처를 두는 대신 테스트가 직접 만든다 — 페이지 수와 문구를 스펙마다 다르게
 * 줄 수 있고, 무엇이 그려져야 하는지가 스펙 안에 그대로 보인다.
 *
 * 압축·인덱스 없이 가장 단순한 형태(PDF 1.4, Type1 표준 폰트)로 쓴다. 전부 ASCII 라
 * 문자열 길이가 곧 바이트 오프셋이어서 xref 표를 정확히 채울 수 있다 — pdf.js 가 망가진
 * xref 를 복구해 주긴 하지만, 그 복구 경로에 기대면 정작 검증하려는 정상 경로를 안 밟는다.
 */

/** PDF 문자열 리터럴에서 특수문자로 취급되는 세 글자를 이스케이프한다. */
function escapeText(text: string): string {
  return text.replace(/([\\()])/g, "\\$1");
}

/** 페이지마다 문구 하나씩 찍힌 PDF 바이트를 만든다. 페이지 크기는 400×560(세로). */
export function makePdfBytes(texts: string[]): Uint8Array {
  const pageCount = texts.length;
  const fontId = 3 + 2 * pageCount;
  const kids = texts.map((_, i) => `${3 + 2 * i} 0 R`).join(" ");

  const bodies: string[] = [];
  bodies[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  bodies[2] = `<< /Type /Pages /Kids [${kids}] /Count ${pageCount} >>`;
  texts.forEach((text, i) => {
    const contentId = 4 + 2 * i;
    bodies[3 + 2 * i] =
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 560] " +
      `/Resources << /Font << /F1 ${fontId} 0 R >> >> /Contents ${contentId} 0 R >>`;
    const stream = `BT /F1 28 Tf 40 480 Td (${escapeText(text)}) Tj ET`;
    bodies[contentId] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });
  bodies[fontId] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  for (let id = 1; id <= fontId; id++) {
    offsets[id] = out.length;
    out += `${id} 0 obj\n${bodies[id]}\nendobj\n`;
  }

  const xrefAt = out.length;
  out += `xref\n0 ${fontId + 1}\n0000000000 65535 f \n`;
  for (let id = 1; id <= fontId; id++) {
    out += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  out += `trailer\n<< /Size ${fontId + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`;

  return new TextEncoder().encode(out);
}

/**
 * 만든 PDF 를 API 로 올리고 파일 id 를 돌려준다.
 * 바이트는 base64 로 브라우저에 넘긴다 — evaluate 인자는 구조화 복제를 거치는데, 문자열이
 * 가장 흔들림 없이 건너간다.
 */
export async function uploadPdf(page: Page, name: string, texts: string[]): Promise<number> {
  const base64 = Buffer.from(makePdfBytes(texts)).toString("base64");
  return page.evaluate(
    async ({ name, base64 }) => {
      const raw = atob(base64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

      const token = localStorage.getItem("minidrive.access_token");
      const form = new FormData();
      form.append("file", new File([bytes], name, { type: "application/pdf" }));
      const res = await fetch("/api/files/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!res.ok) throw new Error(`PDF 업로드 실패: ${res.status} ${await res.text()}`);
      return (await res.json()).id as number;
    },
    { name, base64 },
  );
}

/** 휴지통을 거쳐 완전히 지운다(다른 스펙의 목록을 오염시키지 않도록). */
export async function purgeFile(page: Page, fileId: number): Promise<void> {
  await page.evaluate(async (id) => {
    const token = localStorage.getItem("minidrive.access_token");
    const h = { Authorization: `Bearer ${token}` };
    await fetch(`/api/files/${id}/delete`, { method: "POST", headers: h });
    await fetch(`/api/files/${id}/permanent-delete`, { method: "POST", headers: h });
  }, fileId);
}

/**
 * canvas 에 실제로 뭔가 그려졌는지. 전부 같은 색이면 0 을 돌려준다.
 *
 * "canvas 가 보인다"만 보면 크기 0 이 아닌 빈 사각형도 통과한다 — pdf.js 가 문서를 못 열고
 * 조용히 실패하는 회귀가 정확히 그 모양이라, 픽셀까지 봐야 의미가 있다.
 */
export async function distinctPixelCount(page: Page, selector: string): Promise<number> {
  return page.evaluate((sel) => {
    const canvas = document.querySelector<HTMLCanvasElement>(sel);
    if (!canvas) return -1;
    const ctx = canvas.getContext("2d");
    if (!ctx) return -1;
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const seen = new Set<number>();
    // 4픽셀 간격으로 훑어도 글자 유무는 충분히 갈린다(전수 검사는 큰 canvas 에서 느리다).
    for (let i = 0; i < data.length; i += 16) {
      seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
      if (seen.size > 1) return seen.size;
    }
    return seen.size;
  }, selector);
}
