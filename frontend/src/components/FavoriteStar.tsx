/**
 * 즐겨찾기 별 토글 (Phase 8-2). 목록·그리드 공통 조각.
 *
 * 활성(즐겨찾기됨)이면 항상 accent 색으로 표시하고, 비활성이면 조상의 `group` hover 시에만
 * 나타난다(hover 노출은 부모에 `group` 클래스가 있어야 동작). 클릭은 행 클릭으로 전파되지
 * 않도록 stopPropagation 한다.
 */

import { StarIcon } from "./icons";

export function FavoriteStar({
  active,
  onToggle,
  size = 16,
  className = "",
}: {
  active: boolean;
  onToggle: () => void;
  size?: number;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={active ? "즐겨찾기 해제" : "즐겨찾기"}
      aria-label={active ? "즐겨찾기 해제" : "즐겨찾기"}
      aria-pressed={active}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      className={`rounded-md p-1.5 transition-colors ${
        active
          ? "text-accent"
          : "text-muted opacity-0 hover:text-[color:var(--text-primary)] group-hover:opacity-100"
      } ${className}`}
    >
      <StarIcon width={size} height={size} filled={active} />
    </button>
  );
}
