"""RAG 인덱싱 통합 검증 (실제 postgres[pgvector]+redis+minio 필요, pytest 미수집).

시나리오: admin 부트스트랩 → alice 가입 → 텍스트 업로드 → 인덱싱(워커 태스크 직접 호출,
fake 프로바이더) → file_chunks 생성 확인 → 새 버전 재업로드 → 재인덱싱(구버전 청크 삭제 +
새 버전 청크) → 휴지통 이동 → drop_index(청크 삭제) → 복구 → 재인덱싱(청크 재생성) →
인덱싱 제외 플래그를 폴더에 걸고 하위 파일 재인덱싱 시 조상 상속으로 스킵(청크 없음).

전제: db 는 pgvector 확장을 제공해야 한다(compose db 이미지 pgvector/pgvector:pg16). 인덱싱은
워커 큐를 거치지 않고 서비스 함수를 직접 호출해 결정적으로 검증한다(EMBEDDING_PROVIDER=fake).
업로드 훅의 stray enqueue 를 피하려고 INDEXING_ENABLED=false 로 실행한다.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET, EMBEDDING_PROVIDER=fake,
     INDEXING_ENABLED=false. `python -m tests.integration_indexing`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import FileChunk
from app.services import indexing as indexing_service
from app.services.embeddings import get_embedding_provider
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}

# 청크 분할이 여러 개 나오도록 chunk_size(기본 1000) 를 넘는 텍스트를 만든다.
DOC_V1 = "\n".join(f"버전1 문단 {i}: 미니드라이브 RAG 인덱싱 테스트 내용입니다." for i in range(80))
DOC_V2 = "\n".join(f"버전2 문단 {i}: 완전히 새로운 내용으로 교체되었습니다." for i in range(80))


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()
    if not storage_service._client.bucket_exists(storage_service.bucket):
        storage_service._client.make_bucket(storage_service.bucket)
    leftover = [
        o.object_name
        for o in storage_service._client.list_objects(
            storage_service.bucket, recursive=True
        )
    ]
    storage_service.delete_many(leftover)


async def _chunks(file_id: int) -> list[FileChunk]:
    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(FileChunk)
                .where(FileChunk.file_id == file_id)
                .order_by(FileChunk.version, FileChunk.chunk_index)
            )
        ).scalars().all()
        return list(rows)


async def _index(file_id: int) -> int:
    """워커 태스크와 동일한 경로(index_target)를 fake 프로바이더로 직접 호출한다."""
    provider = get_embedding_provider(settings)
    async with SessionFactory() as s:
        return await indexing_service.index_target(
            s, storage_service, provider, file_id, settings
        )


async def _drop(file_id: int) -> None:
    async with SessionFactory() as s:
        await indexing_service.drop_index(s, file_id)


async def _upload(c: httpx.AsyncClient, h: dict[str, str], name: str, body: bytes,
                  parent_id: int | None = None) -> int:
    data = {"parent_id": str(parent_id)} if parent_id is not None else None
    r = await c.post(
        "/api/files/upload",
        headers=h,
        data=data,
        files={"file": (name, body, "text/plain")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def scenario() -> None:
    assert settings.embedding_provider == "fake", "EMBEDDING_PROVIDER=fake 로 실행하세요."
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, ALICE)
        _ok("셋업 + 코드 가입 → alice 로그인")

        # 1. 텍스트 업로드 → 인덱싱 → file_chunks 생성
        file_id = await _upload(c, alice_h, "doc.txt", DOC_V1.encode())
        indexed = await _index(file_id)
        chunks = await _chunks(file_id)
        assert indexed == 1, indexed
        assert len(chunks) >= 2, f"청크가 여러 개 생성돼야 함: {len(chunks)}"
        assert all(ch.version == 1 for ch in chunks)
        assert all(len(ch.embedding) == 4096 for ch in chunks)
        assert all(ch.chunk_index == i for i, ch in enumerate(chunks))
        _ok(f"업로드 → 인덱싱 (v1 청크 {len(chunks)}개, 4096d)")

        # 2. 멱등성 — 같은 버전 재인덱싱 시 개수 동일(중복 없음)
        await _index(file_id)
        chunks2 = await _chunks(file_id)
        assert len(chunks2) == len(chunks), (len(chunks2), len(chunks))
        _ok("같은 버전 재인덱싱 멱등 (중복 없음)")

        # 3. 새 버전 재업로드 → 재인덱싱 → 구버전 청크 삭제 + 새 버전 청크
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=alice_h,
            data={"base_version": "1"},
            files={"file": ("doc.txt", DOC_V2.encode(), "text/plain")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 2, r.text
        await _index(file_id)
        v_chunks = await _chunks(file_id)
        assert v_chunks, "새 버전 청크가 있어야 함"
        assert all(ch.version == 2 for ch in v_chunks), "구버전(1) 청크가 남으면 안 됨"
        assert "버전2" in v_chunks[0].content, v_chunks[0].content[:40]
        _ok(f"새 버전 재인덱싱 (v2 청크 {len(v_chunks)}개, 구버전 삭제)")

        # 4. 휴지통 이동 → drop_index → 청크 삭제
        r = await c.post(f"/api/files/{file_id}/delete", headers=alice_h)
        assert r.status_code == 204, r.text
        await _drop(file_id)
        assert await _chunks(file_id) == [], "휴지통 이동 후 청크가 남으면 안 됨"
        _ok("휴지통 이동 → drop_index (청크 삭제)")

        # 5. 복구 → 재인덱싱 → 청크 재생성
        r = await c.post(f"/api/files/{file_id}/restore-trash", headers=alice_h)
        assert r.status_code == 200, r.text
        await _index(file_id)
        restored = await _chunks(file_id)
        assert restored and all(ch.version == 2 for ch in restored)
        _ok("복구 → 재인덱싱 (청크 재생성)")

        # 6. 인덱싱 제외 플래그(폴더) → 하위 파일 조상 상속으로 스킵
        r = await c.post("/api/files", headers=alice_h, json={"name": "secret"})
        assert r.status_code == 201, r.text
        folder_id = r.json()["id"]
        child_id = await _upload(c, alice_h, "inside.txt", DOC_V1.encode(), parent_id=folder_id)
        await _index(child_id)
        assert await _chunks(child_id), "제외 전에는 하위 파일이 인덱싱돼야 함"
        _ok("폴더 하위 파일 인덱싱(제외 전)")

        # 폴더에 인덱싱 제외 플래그 설정 (PATCH, 소유자)
        r = await c.patch(
            f"/api/files/{folder_id}", headers=alice_h, json={"indexing_excluded": True}
        )
        assert r.status_code == 200 and r.json()["indexing_excluded"] is True, r.text
        # 재인덱싱 시 조상(폴더) 제외 상속으로 스킵 + 기존 청크 정리
        await _index(child_id)
        assert await _chunks(child_id) == [], "조상 폴더 제외면 하위 청크가 없어야 함"
        _ok("폴더 인덱싱 제외 → 하위 파일 조상 상속 스킵(청크 삭제)")

        # 제외 해제 시 다시 인덱싱 가능
        r = await c.patch(
            f"/api/files/{folder_id}", headers=alice_h, json={"indexing_excluded": False}
        )
        assert r.status_code == 200 and r.json()["indexing_excluded"] is False, r.text
        await _index(child_id)
        assert await _chunks(child_id), "제외 해제 후에는 다시 인덱싱돼야 함"
        _ok("폴더 인덱싱 제외 해제 → 하위 재인덱싱")

    await engine.dispose()
    await redis_client.aclose()
    print("\n인덱싱 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
