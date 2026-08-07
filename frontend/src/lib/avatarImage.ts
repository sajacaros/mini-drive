/**
 * 아바타 이미지 클라이언트 전처리 — 원본이 아무리 커도(수십 MP) 중앙 정사각 crop 후
 * 512x512 로 리사이즈하고 webp(미지원 시 jpeg)로 인코딩한다. 업로드 페이로드를 2MB 이하로
 * 보장하기 위해 초과 시 품질을 단계적으로 낮춘다. 서버 캡(2MB)은 이 변환 결과물 기준.
 */

const OUTPUT_SIZE = 512;
const MAX_BYTES = 2 * 1024 * 1024;

/** 이 브라우저가 canvas → webp 인코딩을 지원하는가. */
function supportsWebp(): boolean {
  try {
    const c = document.createElement("canvas");
    c.width = 1;
    c.height = 1;
    return c.toDataURL("image/webp").startsWith("data:image/webp");
  } catch {
    return false;
  }
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

/** 원본 파일을 512x512 중앙 crop + webp/jpeg 로 인코딩한 Blob 을 반환한다. */
export async function processAvatarFile(file: File): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = OUTPUT_SIZE;
  canvas.height = OUTPUT_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d 컨텍스트를 사용할 수 없습니다.");

  await drawCenterCropped(ctx, file);

  const type = supportsWebp() ? "image/webp" : "image/jpeg";
  let quality = 0.9;
  let blob = await canvasToBlob(canvas, type, quality);
  // 2MB 초과 시 품질을 낮춰 재인코딩 (512px 라 보통 한 번에 통과).
  while (blob && blob.size > MAX_BYTES && quality > 0.4) {
    quality -= 0.1;
    blob = await canvasToBlob(canvas, type, quality);
  }
  if (!blob) throw new Error("이미지를 인코딩하지 못했습니다.");
  return blob;
}

/** 변환 결과 Blob 의 확장자 (파일명 힌트용). */
export function avatarExtension(blob: Blob): string {
  return blob.type.includes("webp") ? "webp" : "jpg";
}

/**
 * 원본을 중앙 정사각 crop 하여 512x512 캔버스에 그린다. 큰 원본에서 메모리를 아끼려고
 * createImageBitmap 의 crop+resize 옵션을 우선 사용하고, 미지원 브라우저는 <img> 폴백.
 */
async function drawCenterCropped(ctx: CanvasRenderingContext2D, file: File): Promise<void> {
  if (typeof createImageBitmap === "function") {
    let source: ImageBitmap | null;
    try {
      source = await createImageBitmap(file);
    } catch {
      source = null; // 디코드 실패 → <img> 폴백.
    }
    if (source) {
      try {
        const side = Math.min(source.width, source.height);
        const sx = (source.width - side) / 2;
        const sy = (source.height - side) / 2;
        try {
          // crop + 512 리사이즈를 디코드 단계에서 처리 (전체 해상도 재보관 회피).
          const cropped = await createImageBitmap(source, sx, sy, side, side, {
            resizeWidth: OUTPUT_SIZE,
            resizeHeight: OUTPUT_SIZE,
            resizeQuality: "high",
          });
          ctx.drawImage(cropped, 0, 0);
          cropped.close();
        } catch {
          // crop 옵션 미지원(구형 Safari 등): 이미 디코드된 비트맵을 직접 축소.
          ctx.drawImage(source, sx, sy, side, side, 0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
        }
        return;
      } finally {
        source.close();
      }
    }
  }
  await drawViaImageElement(ctx, file);
}

/** createImageBitmap 미지원/실패 시 <img> 로 디코드해 중앙 crop 후 그린다. */
function drawViaImageElement(ctx: CanvasRenderingContext2D, file: File): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const sx = (img.naturalWidth - side) / 2;
      const sy = (img.naturalHeight - side) / 2;
      ctx.drawImage(img, sx, sy, side, side, 0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
      URL.revokeObjectURL(url);
      resolve();
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("이미지를 불러오지 못했습니다."));
    };
    img.src = url;
  });
}
