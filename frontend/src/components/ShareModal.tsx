/** 파일 공유 링크 생성 모달 — 만료일/비밀번호/최대 다운로드 옵션 + 생성 URL 복사. */

import { useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { createShare } from "@/api/shares";
import type { FileNode, Share, SharePermission } from "@/api/types";
import { Modal } from "./Modal";
import { Spinner } from "./ui";
import { CopyIcon } from "./icons";
import { useToast } from "./Toast";

/** share_url 로 사용자에게 보여줄 공개 링크를 만든다 (PRD: {origin}/s/{share_url}). */
export function buildShareLink(shareUrl: string): string {
  return `${window.location.origin}/s/${shareUrl}`;
}

export function ShareModal({
  file,
  open,
  onClose,
  onCreated,
}: {
  file: FileNode | null;
  open: boolean;
  onClose: () => void;
  onCreated?: (share: Share) => void;
}) {
  const toast = useToast();
  const [permission, setPermission] = useState<SharePermission>("download");
  const [expiresAt, setExpiresAt] = useState("");
  const [password, setPassword] = useState("");
  const [maxDownloads, setMaxDownloads] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<Share | null>(null);

  const reset = () => {
    setPermission("download");
    setExpiresAt("");
    setPassword("");
    setMaxDownloads("");
    setCreated(null);
    setSubmitting(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const onSubmit = async () => {
    if (!file) return;
    setSubmitting(true);
    try {
      const share = await createShare({
        file_id: file.id,
        permission,
        // datetime-local(로컬 시각) → ISO 문자열로 변환해 전송.
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
        password: password || null,
        max_downloads: maxDownloads ? Number(maxDownloads) : null,
      });
      setCreated(share);
      onCreated?.(share);
    } catch (err) {
      toast.error(extractErrorMessage(err, "공유 링크 생성에 실패했습니다."));
    } finally {
      setSubmitting(false);
    }
  };

  const copyLink = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(buildShareLink(created.share_url));
      toast.success("링크를 복사했습니다.");
    } catch {
      toast.error("클립보드 복사에 실패했습니다.");
    }
  };

  return (
    <Modal
      open={open}
      title={created ? "공유 링크 생성 완료" : "공유 링크 생성"}
      onClose={handleClose}
      footer={
        created ? (
          <button className="btn btn-primary" onClick={handleClose}>
            완료
          </button>
        ) : (
          <>
            <button className="btn btn-secondary" onClick={handleClose}>
              취소
            </button>
            <button className="btn btn-primary" onClick={onSubmit} disabled={submitting}>
              {submitting ? <Spinner className="h-4 w-4" /> : "생성"}
            </button>
          </>
        )
      }
    >
      {created ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted">
            <span className="font-medium text-[color:var(--text-primary)]">{created.file_name}</span>{" "}
            공유 링크가 생성되었습니다.
          </p>
          <div className="flex items-center gap-2">
            <input className="input font-mono text-xs" readOnly value={buildShareLink(created.share_url)} />
            <button className="btn btn-secondary shrink-0" onClick={copyLink} aria-label="링크 복사">
              <CopyIcon width={16} height={16} />
            </button>
          </div>
          {created.password_required && (
            <p className="text-xs text-muted">비밀번호가 설정된 링크입니다.</p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="truncate text-sm text-muted">
            대상 파일: <span className="font-medium text-[color:var(--text-primary)]">{file?.name}</span>
          </p>

          <div>
            <label className="label">권한</label>
            <select
              className="input"
              value={permission}
              onChange={(e) => setPermission(e.target.value as SharePermission)}
            >
              <option value="download">다운로드 허용</option>
              <option value="read">읽기 전용</option>
            </select>
          </div>

          <div>
            <label className="label" htmlFor="expiresAt">
              만료일 (선택)
            </label>
            <input
              id="expiresAt"
              type="datetime-local"
              className="input"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
          </div>

          <div>
            <label className="label" htmlFor="sharePw">
              비밀번호 (선택)
            </label>
            <input
              id="sharePw"
              type="password"
              className="input"
              placeholder="설정하지 않으면 비워 두세요"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>

          <div>
            <label className="label" htmlFor="maxDl">
              최대 다운로드 횟수 (선택)
            </label>
            <input
              id="maxDl"
              type="number"
              min={1}
              className="input"
              placeholder="제한 없음"
              value={maxDownloads}
              onChange={(e) => setMaxDownloads(e.target.value)}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}
