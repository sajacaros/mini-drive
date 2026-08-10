"""정합 스윕 통합 검증 (spec/wiki-index.md 「정합 스윕」).

판정 로직 자체는 `test_wiki_reconcile_unit.py` 가 DB 없이 고정한다. 여기서 검증하는 것은
단위 테스트가 닿지 못하는 두 가지다.

  1. **유효 상태 도출 SQL**(`wiki._ENABLED_FILES_SQL`). 상속·명시 OFF 탈출구·중첩 폴더·
     휴지통이 재귀 질의 한 방으로 `resolve_wiki_state` 와 같은 답을 내는가. 이게 틀리면
     스윕이 멀쩡한 문서를 매일 내리거나 되살린다.
  2. **적용 경로**. 판정 결과가 실제 행 상태와 Redis 큐에 그대로 반영되는가.

시나리오는 훅을 일부러 건너뛰어 드리프트를 만든 뒤(=fail-open 으로 예약이 샌 상황을 재현),
스윕이 그것을 메우는지 본다.

    python -m tests.integration_wiki_reconcile

**dev DB 를 비우고 시작한다.**
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, update

from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import File, WikiDocument
from app.services import wiki as wiki_service
from app.services import wiki_indexer, wiki_queue
from app.services.storage import get_storage, storage_service
from tests._bootstrap import setup_admin
from tests._dbreset import stamp_alembic_head

MD = """# 운영 노트

이 문서는 운영 중 알아두면 좋은 것을 모은다.

## 로그

구조화 로깅이라 request_id 로 추적한다. 사이드카는 자기 포트로 메트릭을 연다. 로그는
하루 단위로 회전하고, 보존 기간이 지나면 자동으로 지워진다. 검색은 필드 단위로 한다.

## 배포

배포 전에 스테이징에서 스모크 테스트를 돌린다. 마이그레이션이 있으면 롤백 계획을 먼저
세우고, 비밀 값은 환경변수로만 주입한다. 이미지에 굽지 않는다.
"""

MD_V2 = MD + """
## 모니터링

배포 직후 5분간 에러율과 지연을 지켜본다. 임계값을 넘으면 이전 버전으로 되돌린다.
"""


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


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent: int, body: bytes
) -> int:
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, body, "application/octet-stream")},
        data={"parent_id": str(parent)},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _drain() -> dict[str, int]:
    """디바운스를 무시하고 큐를 즉시 비운다."""
    pending = await redis_client.zrange(wiki_queue.QUEUE_KEY, 0, -1)
    if pending:
        await redis_client.zadd(wiki_queue.QUEUE_KEY, {m: 0 for m in pending})
    async with SessionFactory() as session:
        return await wiki_indexer.run_once(session, get_storage(), limit=20)


async def _status(file_id: int) -> str | None:
    async with SessionFactory() as session:
        return (
            await session.execute(
                select(WikiDocument.status).where(WikiDocument.file_id == file_id)
            )
        ).scalars().first()


async def _sweep() -> wiki_indexer.ReconcileResult:
    async with SessionFactory() as session:
        return await wiki_indexer.reconcile(session)


async def _enabled_ids() -> set[int]:
    async with SessionFactory() as session:
        return {r.file_id for r in await wiki_service.enabled_file_rows(session)}


async def main() -> None:  # noqa: PLR0915 - 순차 시나리오
    await _reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        h, _ = await setup_admin(c)

        # ── 1. 유효 상태 도출 SQL ──────────────────────────────────────
        #
        # 켜진 폴더 / 그 안의 중첩 폴더 / 명시 OFF 탈출구 / 꺼진 폴더 / 휴지통.
        # 재귀 질의 하나가 resolve_wiki_state 와 같은 답을 내야 한다.
        wiki_folder = (await c.post("/api/files", headers=h, json={"name": "위키함"})).json()["id"]
        nested = (
            await c.post(
                "/api/files", headers=h, json={"name": "하위", "parent_id": wiki_folder}
            )
        ).json()["id"]
        other = (await c.post("/api/files", headers=h, json={"name": "일반함"})).json()["id"]

        inherited = await _upload(c, h, "inherited.md", wiki_folder, MD.encode())
        deep = await _upload(c, h, "deep.md", nested, MD.encode())
        optout = await _upload(c, h, "optout.md", wiki_folder, MD.encode())
        pdf = await _upload(c, h, "manual.pdf", wiki_folder, b"%PDF-1.4 fake")
        outside = await _upload(c, h, "outside.md", other, MD.encode())
        trashed = await _upload(c, h, "trashed.md", wiki_folder, MD.encode())

        r = await c.put(
            f"/api/files/{wiki_folder}/wiki", headers=h, json={"enabled": True}
        )
        assert r.status_code == 200, r.text
        # 소유자 탈출구 — 켜진 폴더 안에서 이 파일만 명시 OFF.
        r = await c.put(f"/api/files/{optout}/wiki", headers=h, json={"enabled": False})
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/files/{trashed}/delete", headers=h)
        assert r.status_code == 204, r.text

        enabled = await _enabled_ids()
        assert inherited in enabled, "켠 폴더 바로 아래 파일이 빠졌다"
        assert deep in enabled, "중첩 폴더의 파일이 빠졌다 (상속이 한 단계에서 끊긴다)"
        assert pdf in enabled, "도출은 형식을 보지 않는다 — 거르는 것은 호출자다"
        assert optout not in enabled, "명시 OFF 탈출구가 무시됐다"
        assert outside not in enabled, "꺼진 폴더의 파일이 잡혔다"
        assert trashed not in enabled, "휴지통 항목이 잡혔다"
        assert wiki_folder not in enabled and nested not in enabled, "폴더가 잡혔다"
        _ok("유효 상태 도출 — 상속·중첩·명시 OFF·휴지통·폴더 제외가 맞다")

        # 도출 결과가 파일별 판정과 일치하는지 교차 확인. 두 경로가 갈리면 스윕이 매일
        # 무언가를 되돌린다.
        async with SessionFactory() as session:
            for fid in (inherited, deep, optout, pdf, outside):
                one = (await wiki_service.resolve_wiki_state(session, fid)).enabled
                assert one == (fid in enabled), f"file {fid}: 도출과 파일별 판정이 다르다"
        _ok("도출 SQL 과 resolve_wiki_state 가 같은 답을 낸다")

        await _drain()
        assert await _status(inherited) == "ready", await _status(inherited)
        assert await _status(deep) == "ready"

        # ── 2. 휴지통으로 간 문서의 행이 남아 있던 것을 내린다 ────────
        #
        # 실측(2026-08-10): 휴지통 경로에는 위키 훅이 없어 색인 행이 `pending` 그대로 남는다.
        # 워커는 is_deleted 를 보고 건너뛰므로 그 행은 영원히 pending 이었다. spec 이 "파일이
        # 휴지통으로 가면 트리도 즉시 비활성"이라고 적은 것이 실제로는 성립하지 않던 자리이고,
        # 스윕이 하루 안에 그것을 맞춘다.
        assert await _status(trashed) == "pending", await _status(trashed)
        drift = await _sweep()
        assert drift.disabled == 1 and drift.enqueued == 0, drift
        assert await _status(trashed) == "disabled"
        _ok("휴지통 항목의 pending 행을 내린다 (훅이 없던 자리)")

        # ── 3. 정상 상태에서는 아무 것도 하지 않는다 ──────────────────
        drift = await _sweep()
        assert drift.enqueued == 0 and drift.disabled == 0, drift
        assert await wiki_queue.pending_count() == 0
        _ok("정상 상태 — 스윕이 아무 것도 건드리지 않는다")

        # ── 3. 구멍 ① ready 인데 버전이 뒤짐 ──────────────────────────
        #
        # 훅(_enqueue_wiki_reindex)이 fail-open 으로 통째로 실패한 상황을 재현한다 —
        # 새 버전은 올라갔는데 stale 표시도 큐 투입도 없는 상태.
        r = await c.post(
            f"/api/files/{inherited}/upload",
            headers=h,
            files={"file": ("inherited.md", MD_V2.encode(), "text/markdown")},
        )
        assert r.status_code == 201, r.text
        async with SessionFactory() as session:
            await session.execute(
                update(WikiDocument)
                .where(WikiDocument.file_id == inherited)
                .values(status=wiki_service.WikiStatus.READY)
            )
            await session.commit()
        await wiki_queue.drop([inherited])
        assert await _status(inherited) == "ready", "드리프트 재현 실패"

        drift = await _sweep()
        assert drift.outdated == 1, drift
        assert await _status(inherited) == "stale", await _status(inherited)
        _ok("구멍 ① — ready 인데 낡은 트리를 찾아 stale + 재색인 예약")

        await _drain()
        assert await _status(inherited) == "ready"

        # ── 4. 구멍 ② 행이 아예 없음 ──────────────────────────────────
        async with SessionFactory() as session:
            await session.execute(
                delete(WikiDocument).where(WikiDocument.file_id == deep)
            )
            await session.commit()
        await wiki_queue.drop([deep])

        drift = await _sweep()
        assert drift.missing == 1, drift
        assert await _status(deep) == "pending", await _status(deep)
        _ok("구멍 ② — 켜져 있는데 행이 없던 문서를 되살린다")
        await _drain()
        assert await _status(deep) == "ready"

        # ── 5. 구멍 ③ 큐에서 사라진 pending ───────────────────────────
        newcomer = await _upload(c, h, "newcomer.md", wiki_folder, MD.encode())
        await redis_client.flushdb()  # Redis flush — 예약이 통째로 사라진다
        assert await _status(newcomer) == "pending"

        drift = await _sweep()
        assert drift.stuck >= 1, drift
        assert await wiki_queue.pending_count() >= 1
        await _drain()
        assert await _status(newcomer) == "ready"
        _ok("구멍 ③ — Redis flush 로 사라진 예약을 회수한다")

        # ── 6. 구멍 ④ failed 재시도와 상한 ────────────────────────────
        async with SessionFactory() as session:
            await session.execute(
                update(WikiDocument)
                .where(WikiDocument.file_id == newcomer)
                .values(
                    status=wiki_service.WikiStatus.FAILED,
                    attempts=1,
                    last_attempt_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            await session.commit()

        drift = await _sweep()
        assert drift.retried == 1, drift
        await _drain()
        assert await _status(newcomer) == "ready", "재시도가 복구로 이어지지 않았다"
        _ok("구멍 ④ — failed 를 다시 태워 복구한다")

        async with SessionFactory() as session:
            await session.execute(
                update(WikiDocument)
                .where(WikiDocument.file_id == newcomer)
                .values(
                    status=wiki_service.WikiStatus.FAILED,
                    attempts=99,
                    last_attempt_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            await session.commit()
        drift = await _sweep()
        assert drift.retried == 0 and drift.exhausted == 1, drift
        _ok("구멍 ④ — 상한을 넘긴 실패는 매일 다시 굽지 않는다")

        # 사람이 다시 밀어 넣는 탈출구 — 재토글이 카운터를 0 으로 되돌린다.
        r = await c.put(f"/api/files/{newcomer}/wiki", headers=h, json={"enabled": True})
        assert r.status_code == 200, r.text
        await _drain()
        assert await _status(newcomer) == "ready"
        _ok("재토글이 재시도 카운터를 되돌린다 (상한의 탈출구)")

        # ── 7. 구멍 ⑤ 유효 OFF 인데 질의 대상 ─────────────────────────
        #
        # 폴더를 껐는데 훅이 하위까지 닿지 못한 상황을 재현한다.
        async with SessionFactory() as session:
            folder_row = await session.get(File, wiki_folder)
            assert folder_row is not None
            folder_row.wiki_enabled = False
            folder_row.wiki_disabled_at = datetime.now(UTC)
            await session.commit()
        # 상태는 일부러 그대로 둔다 — 훅이 샌 상태다.
        assert await _status(inherited) == "ready"

        drift = await _sweep()
        assert drift.disabled >= 2, drift
        assert await _status(inherited) == "disabled", await _status(inherited)
        assert await _status(deep) == "disabled"
        _ok("구멍 ⑤ — 유효 OFF 인데 질의 대상이던 문서를 즉시 내린다")

        # 되돌리면 트리 그대로 ready 로 복구된다 — 대량 오판이 나도 다음 회차가 회복한다.
        async with SessionFactory() as session:
            folder_row = await session.get(File, wiki_folder)
            assert folder_row is not None
            folder_row.wiki_enabled = True
            folder_row.wiki_disabled_at = None
            await session.commit()
        drift = await _sweep()
        assert drift.revived >= 2, drift
        assert await _status(inherited) == "ready", "재색인 없이 복구돼야 한다"
        _ok("부활 — 트리와 버전이 그대로면 비용 0 으로 ready 복구")

        # ── 8. 멱등 ───────────────────────────────────────────────────
        await _drain()
        first = await _sweep()
        second = await _sweep()
        assert first.enqueued == 0 and second.enqueued == 0, (first, second)
        _ok("멱등 — 연속 실행이 아무 것도 만들지 않는다")

    await engine.dispose()
    print("\n정합 스윕 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
