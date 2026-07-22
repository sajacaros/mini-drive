"""폴더/다중 선택 ZIP 다운로드 (스트리밍 아카이브).

게이트웨이 다운로드 모델(PRD 2.2)은 오브젝트 **하나**를 nginx 가 X-Accel-Redirect 로 직접
흘려보내는 구조라 여러 오브젝트를 한 응답으로 묶을 수 없다. 그래서 아카이브만은 backend 가
스트리밍 주체가 된다: MinIO 에서 청크로 읽어 그대로 ZIP 프레임에 실어 내보내고, 전체를
메모리나 디스크에 만들지 않는다. 압축은 하지 않는다(ZIP_STORED) — 이미 압축된 파일이 대부분인
드라이브에서 CPU 만 태우고 이득이 없기 때문이며, 무압축이라 읽은 바이트가 곧 나가는 바이트다.

인가는 두 번 판정한다 — 티켓 발급 시(에러를 HTTP 상태로 돌려줄 수 있는 유일한 시점)와
티켓 소비 시(스트리밍 직전 재판정). 첫 바이트를 내보낸 뒤에는 상태 코드를 바꿀 수 없으므로
접근 판정·개수/용량 상한은 반드시 스트리밍 전에 끝낸다.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Buffer, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import File, User
from app.services import permissions as permissions_service
from app.services.files import FileServiceError, ensure_file_access, get_file
from app.services.storage import StorageService

_log = get_logger("app.archives")

_KST = ZoneInfo("Asia/Seoul")

# 아카이브 상한 — 스트리밍 중에는 실패를 알릴 방법이 없으므로 계획 단계에서 자른다.
# 백엔드 워커 하나가 이 크기를 다 흘려보낼 때까지 묶이므로, 넉넉함보다 회전율을 택했다.
MAX_ARCHIVE_FILES = 500
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

# MinIO → ZIP 릴레이 청크. 이 크기만큼만 메모리에 머문다.
_STREAM_CHUNK = 1024 * 1024

# ZIP 로컬 헤더의 최소 연도 — 그보다 이른 타임스탬프는 표현할 수 없다.
_ZIP_MIN_YEAR = 1980


@dataclass(frozen=True)
class ArchiveEntry:
    """ZIP 안의 항목 하나. key 가 None 이면 폴더 엔트리(빈 폴더 보존용)."""

    relpath: str
    key: str | None
    size: int
    modified: datetime


@dataclass(frozen=True)
class ArchivePlan:
    """스트리밍 전에 확정한 아카이브 내용. 여기서 상한 위반과 접근 불가가 모두 걸러진다."""

    entries: list[ArchiveEntry]
    filename: str
    total_bytes: int
    file_count: int


# 선택한 루트 아래 트리를 상대 경로와 함께 한 번에 펼친다. 삭제(휴지통) 항목은 가지째 제외하고,
# 폴더가 아닌 노드 밑으로는 재귀하지 않는다. relpath 의 시작점(:prefix)은 호출자가 정한 최상위
# 표시 이름이다 — 같은 요청 안에서 이름이 겹칠 때 " (2)" 를 붙여 넘기기 때문이다.
_TREE_SQL = text(
    """
    WITH RECURSIVE tree AS (
        SELECT id, name, is_folder, file_key, size, updated_at,
               CAST(:prefix AS text) AS relpath
        FROM files
        WHERE id = :root AND is_deleted = FALSE
        UNION ALL
        SELECT f.id, f.name, f.is_folder, f.file_key, f.size, f.updated_at,
               t.relpath || '/' || f.name
        FROM files f
        JOIN tree t ON f.parent_folder_id = t.id
        WHERE f.is_deleted = FALSE AND t.is_folder
    )
    SELECT is_folder, file_key, size, updated_at, relpath FROM tree ORDER BY relpath
    """
)


def _unique_name(name: str, used: set[str]) -> str:
    """최상위 이름 충돌을 피한다. 형제끼리는 이름이 유일하지만, 여러 폴더에서 모아 담는
    경우(향후 확장)나 예외적인 데이터에서도 ZIP 안에서 덮어쓰이지 않게 방어한다."""
    if name not in used:
        used.add(name)
        return name
    base, dot, ext = name.rpartition(".")
    for n in range(2, 1000):
        candidate = f"{base} ({n}).{ext}" if dot else f"{name} ({n})"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise FileServiceError(409, "이름이 겹치는 항목이 너무 많습니다.")


def _archive_filename(roots: Sequence[File]) -> str:
    """받는 쪽에서 무엇인지 알아볼 수 있는 zip 이름.

    폴더 하나만 골랐으면 그 폴더 이름(가장 흔한 경우), 그 밖에는 시각을 붙인 기본 이름.
    """
    if len(roots) == 1 and roots[0].is_folder:
        return f"{roots[0].name}.zip"
    stamp = datetime.now(_KST).strftime("%Y%m%d-%H%M%S")
    return f"flex-drive-{stamp}.zip"


async def plan_archive(
    session: AsyncSession, user: User, file_ids: Iterable[int]
) -> ArchivePlan:
    """선택한 항목들을 ZIP 내용으로 펼친다 (접근 판정 + 상한 검사 포함).

    폴더는 하위 전체를 상대 경로로 펼치고, 빈 폴더도 엔트리로 남겨 구조를 보존한다.
    상한(개수/용량) 초과는 413, 접근 불가는 ensure_file_access 와 같은 404 로 통일한다.
    """
    ids = list(dict.fromkeys(file_ids))
    if not ids:
        raise FileServiceError(400, "다운로드할 항목을 선택해 주세요.")
    if len(ids) > MAX_ARCHIVE_FILES:
        raise FileServiceError(
            413, f"한 번에 {MAX_ARCHIVE_FILES}개까지 내려받을 수 있습니다."
        )

    roots: list[File] = []
    for file_id in ids:
        file = await ensure_file_access(session, user, await get_file(session, file_id))
        if file.is_deleted:
            raise FileServiceError(404, "파일을 찾을 수 없습니다.")
        roots.append(file)

    entries: list[ArchiveEntry] = []
    used_top: set[str] = set()
    total_bytes = 0
    file_count = 0

    def _add(relpath: str, key: str | None, size: int, modified: datetime) -> None:
        nonlocal total_bytes, file_count
        if key is not None:
            file_count += 1
            total_bytes += size
            if file_count > MAX_ARCHIVE_FILES:
                raise FileServiceError(
                    413,
                    f"한 번에 파일 {MAX_ARCHIVE_FILES}개까지 내려받을 수 있습니다. "
                    "폴더를 나눠 받아 주세요.",
                )
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise FileServiceError(
                    413,
                    f"한 번에 {MAX_ARCHIVE_BYTES // (1024 * 1024 * 1024)}GB 까지 "
                    "내려받을 수 있습니다. 폴더를 나눠 받아 주세요.",
                )
        entries.append(ArchiveEntry(relpath, key, size, modified))

    for file in roots:
        top = _unique_name(file.name, used_top)
        if not file.is_folder:
            if file.file_key:
                _add(top, file.file_key, file.size, file.updated_at)
            continue

        # 폴더를 읽을 수 있다고 하위까지 읽을 수 있는 건 아니다 (inherit_to_children=False).
        # 그 경우 폴더 자체만 빈 채로 담는다 — 없는 권한을 조용히 넘겨주지 않는다.
        if not await permissions_service.can_access_descendants(session, user, file):
            _add(f"{top}/", None, 0, file.updated_at)
            continue

        rows = (
            await session.execute(_TREE_SQL, {"root": file.id, "prefix": top})
        ).all()
        for row in rows:
            if row.is_folder:
                _add(f"{row.relpath}/", None, 0, row.updated_at)
            elif row.file_key:
                _add(row.relpath, row.file_key, row.size, row.updated_at)

    return ArchivePlan(
        entries=entries,
        filename=_archive_filename(roots),
        total_bytes=total_bytes,
        file_count=file_count,
    )


class _ZipSink(io.RawIOBase):
    """zipfile 이 쓰는 바이트를 모아 두는 비탐색(unseekable) 싱크.

    seek/tell 을 지원하지 않으므로 zipfile 이 스트리밍 모드(데이터 디스크립터)로 동작한다.
    write 로 쌓인 바이트를 drain 으로 꺼내 그대로 응답에 흘려보낸다.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b: Buffer) -> int:
        data = bytes(b)
        self._chunks.append(data)
        return len(data)

    def drain(self) -> bytes:
        if not self._chunks:
            return b""
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def _zip_date_time(moment: datetime) -> tuple[int, int, int, int, int, int]:
    """ZIP 로컬 헤더용 (Y, M, D, h, m, s). 표시 시각은 KST 기준."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    local = aware.astimezone(_KST)
    if local.year < _ZIP_MIN_YEAR:
        return (_ZIP_MIN_YEAR, 1, 1, 0, 0, 0)
    return (local.year, local.month, local.day, local.hour, local.minute, local.second)


def stream_archive(storage: StorageService, entries: Sequence[ArchiveEntry]) -> Iterator[bytes]:
    """계획된 엔트리를 ZIP 바이트 스트림으로 만든다 (동기 제너레이터).

    MinIO SDK 가 동기라 이 함수도 동기이며, Starlette 가 스레드풀에서 돌린다. 한 청크를
    읽을 때마다 ZIP 프레임을 만들어 즉시 내보내므로 상주 메모리는 청크 크기 수준이다.
    스트리밍이 시작된 뒤의 실패(오브젝트 유실 등)는 상태 코드로 알릴 수 없어 연결이 끊긴다 —
    로그를 남기고 중단한다.
    """
    sink = _ZipSink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.relpath, date_time=_zip_date_time(entry.modified))
            info.compress_type = zipfile.ZIP_STORED
            if entry.key is None:
                # 폴더 엔트리 — 이름이 "/" 로 끝나고 내용이 없다.
                info.external_attr = (0o040755 << 16) | 0x10
                zf.writestr(info, b"")
                yield sink.drain()
                continue

            info.file_size = entry.size
            response = storage.get(entry.key)
            try:
                with zf.open(info, "w") as dst:
                    for chunk in response.stream(_STREAM_CHUNK):
                        dst.write(chunk)
                        out = sink.drain()
                        if out:
                            yield out
            except Exception:  # noqa: BLE001 - 스트리밍 중 실패는 연결 종료로만 알린다
                _log.exception("archive_stream_failed", relpath=entry.relpath)
                raise
            finally:
                response.close()
                response.release_conn()
            yield sink.drain()
    # 중앙 디렉터리(닫을 때 기록됨)까지 내보낸다.
    tail = sink.drain()
    if tail:
        yield tail
