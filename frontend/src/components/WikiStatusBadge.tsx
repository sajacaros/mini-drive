/**
 * 위키 인덱싱 상태 배지 (spec/wiki-index.md).
 *
 * 모달과 파일 목록이 같은 표기를 쓰도록 한 곳에 둔다. 상태는 백엔드가 null 없는 총체 함수로
 * 주므로(off|pending|indexing|ready|stale|failed) 여기서 분기가 새지 않는다.
 */

import type { WikiStatus } from "@/api/types";
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
