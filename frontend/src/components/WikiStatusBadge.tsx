/**
 * 위키 표기 (spec/wiki-index.md) — 두 가지가 있다.
 *
 *  - `WikiMark`        목록·그리드·이동 선택기: **책 아이콘 하나**. 폴더든 파일이든 같은 표식이다.
 *  - `WikiStatusBadge` 위키 설정 모달·문서 카탈로그: 상태를 글로 말하는 배지.
 *
 * 상태는 백엔드가 null 없는 총체 함수로 주므로(off|pending|indexing|ready|stale|failed)
 * 여기서 분기가 새지 않는다.
 */

import type { FileNode, WikiStatus } from "@/api/types";
import { BookIcon } from "./icons";
import { Badge } from "./ui";

interface Descriptor {
  label: string;
  tone: "neutral" | "success" | "warning" | "danger" | "accent";
  /** 배지 옆에 덧붙일 설명 — 사용자가 "왜 아직 안 나오지?"를 묻지 않게 한다. */
  hint?: string;
}

const DESCRIPTORS: Record<WikiStatus, Descriptor> = {
  off: { label: "위키 미포함", tone: "neutral" },
  pending: {
    label: "인덱싱 대기",
    tone: "warning",
    hint: "잠시 후 자동으로 시작됩니다.",
  },
  indexing: { label: "인덱싱 중", tone: "warning", hint: "완료되면 자동으로 갱신됩니다." },
  ready: { label: "위키 포함", tone: "success" },
  // 구 트리로 계속 답하므로 검색이 멈추지 않는다 — 사용자를 불안하게 만들지 않는 문구를 쓴다.
  stale: {
    label: "새 버전 반영 중",
    tone: "warning",
    hint: "그동안은 이전 버전 내용으로 답합니다.",
  },
  failed: { label: "인덱싱 실패", tone: "danger" },
  // 껐지만 트리는 유예 동안 남아 있는 상태. 문서 카탈로그 목록에만 나온다 — 목록에서 사라지면
  // 소유자가 "왜 빠졌는지" 알 수 없어서 상태 그대로 남기기로 한 결정이다(spec/wiki-index.md).
  disabled: {
    label: "위키 꺼짐",
    tone: "neutral",
    hint: "검색 대상에서 빠졌습니다. 다시 켜면 재색인 없이 돌아옵니다.",
  },
};

export function wikiStatusLabel(status: WikiStatus): string {
  return DESCRIPTORS[status].label;
}

export function wikiStatusHint(status: WikiStatus): string | undefined {
  return DESCRIPTORS[status].hint;
}

export function WikiStatusBadge({
  status,
  nodeCount,
}: {
  status: WikiStatus;
  /** ready 일 때만 의미가 있다 — 트리 규모를 한눈에 보여준다. */
  nodeCount?: number | null;
}) {
  const d = DESCRIPTORS[status];
  const suffix =
    status === "ready" && nodeCount != null ? ` · 절 ${nodeCount}개` : "";
  return (
    <Badge tone={d.tone}>
      {d.label}
      {suffix}
    </Badge>
  );
}

/** 목록 표식의 상태별 색과 설명. 여기 없는 상태(off·disabled)는 아무것도 그리지 않는다. */
const MARKS: Partial<Record<WikiStatus, { className: string; title: string }>> = {
  ready: { className: "text-accent", title: "전사 위키 — 모든 구성원이 열람할 수 있습니다." },
  // 트리는 구버전이지만 위키에 들어 있다는 사실은 같다. 목록에서는 같은 표식으로 둔다.
  stale: {
    className: "text-accent",
    title: "전사 위키 — 새 버전 반영 중입니다(그동안은 이전 버전 내용으로 답합니다).",
  },
  pending: { className: "text-muted", title: "위키 인덱싱을 기다리는 중입니다." },
  indexing: { className: "text-muted", title: "위키 인덱싱 중입니다." },
  failed: { className: "text-danger", title: "위키 인덱싱에 실패했습니다." },
};

/**
 * 목록에서 "이건 위키다"를 말하는 표식 — **책 아이콘 하나** (spec/wiki-index.md).
 *
 * 폴더의 '전사 위키'와 파일의 '위키 포함'은 사용자에게 같은 사실이다. 목록에서 단어를 둘로
 * 나누면 개념이 둘로 보인다. 그래서 표식은 하나로 두고 색으로만 진행 상태를 덧붙인다 —
 * 들어와 있으면 accent, 아직 색인 중이면 흐리게, 실패면 danger. **정확한 상태와 사유는
 * 위키 설정 모달과 문서 카탈로그가 글로 말한다**(거기는 자리가 있고 읽으러 간 화면이다).
 */
export function WikiMark({ file }: { file: FileNode }) {
  // 폴더에는 인덱싱 상태가 없다(트리는 문서당 하나) — 선언 자체가 곧 위키 폴더다.
  const mark = file.is_folder
    ? file.wiki_declared === true
      ? {
          className: "text-accent",
          title: "전사 위키 폴더 — 여기 담긴 Markdown·HTML 문서는 모든 구성원이 열람할 수 있습니다.",
        }
      : undefined
    : MARKS[file.wiki_status ?? "off"];
  if (!mark) return null;
  // 이름 **앞**, 글자와 같은 줄에. 이름 뒤에 두면 이름 길이를 따라 표식 위치가 제각각이라
  // 훑을 때 눈이 한 줄로 못 내려간다.
  //
  // 맨몸 아이콘이다. 윗첨자·회전·테두리 상자를 차례로 시도했다가 전부 되돌렸다 —
  //  · 윗첨자(작게+위로): 선이 픽셀 격자를 비껴가 형태가 뭉갰다.
  //  · 테두리 상자: 유형 아이콘도 외곽선이라 선이 세 겹으로 빽빽해지고, 1px 테두리는 화면
  //    배율이 100%가 아니면 변마다 굵기가 갈린다(CSS 로 못 막는다).
  // 유형 아이콘과 섞이지 않게 하는 일은 **자리**가 한다 — 이름에 6px, 유형 아이콘에 10px 이라
  // 이름 쪽으로 묶여 읽힌다.
  return (
    <span
      className={`mr-1.5 inline-block shrink-0 align-middle ${mark.className}`}
      title={mark.title}
      aria-label={mark.title}
    >
      <BookIcon width={15} height={15} />
    </span>
  );
}
