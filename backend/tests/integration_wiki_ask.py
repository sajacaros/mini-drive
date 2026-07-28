"""위키 질의 통합 검증 (spec/wiki-index.md). **실제 사내 vLLM 을 호출한다.**

검증 축:
  1. 인덱싱된 문서에 질문하면 근거와 함께 답이 나온다.
  2. **권한 필터가 대상 선정 단계에 걸린다** — 권한 없는 사용자는 그 문서를 검색조차 못 한다.
  3. 전사 공개 후에는 같은 질문이 답을 얻는다.
  4. 자료에 없는 질문에는 **존재를 부정하지 않고** "접근 가능한 자료 중에는 없습니다"로 답한다.
  5. 근거는 파일·노드·줄 번호를 가리킨다(미리보기 앵커).
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.services import wiki_indexer, wiki_queue
from app.services.storage import get_storage, storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

DOC = """# 배포 운영 가이드

이 문서는 서비스 배포와 장애 대응 절차를 정리한다.

## 배포 승인 절차

배포는 팀 리드 승인 후에만 진행한다. 금요일 오후 4시 이후에는 정기 배포를 하지 않는다.
긴급 수정은 예외이며, 이 경우 사후에 배포 사유를 회고 문서에 남긴다.

## 롤백 기준

배포 후 10분 안에 에러율이 2%를 넘으면 즉시 직전 태그로 롤백한다. 데이터 마이그레이션이
포함된 배포는 스키마 호환성을 먼저 확인한 뒤 롤백 여부를 판단한다.

## 장애 등급

P1 은 전체 사용자 영향, P2 는 일부 기능 불가, P3 는 우회 가능한 불편으로 구분한다.
P1 은 즉시 전사 공지하고 P2 는 담당 팀 채널에 알린다.
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


async def _drain() -> dict[str, int]:
    pending = await redis_client.zrange(wiki_queue.QUEUE_KEY, 0, -1)
    if pending:
        await redis_client.zadd(wiki_queue.QUEUE_KEY, {m: 0 for m in pending})
    async with SessionFactory() as session:
        return await wiki_indexer.run_once(session, get_storage(), limit=20)


async def main() -> None:
    await _reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=180) as c:
        alice_h, code = await setup_admin(c)
        bob_h, _ = await register_active(c, code, BOB)

        r = await c.post("/api/files", headers=alice_h, json={"name": "운영"})
        folder = r.json()["id"]
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("deploy-ops.md", DOC.encode(), "text/markdown")},
            data={"parent_id": str(folder)},
        )
        fid = r.json()["id"]
        r = await c.put(f"/api/files/{fid}/wiki", headers=alice_h, json={"enabled": True})
        assert r.status_code == 200, r.text
        counts = await _drain()
        assert counts.get("ready") == 1, counts
        _ok("문서 인덱싱 완료")

        # 1. 소유자 질의 — 근거와 함께 답이 나온다
        r = await c.post(
            "/api/wiki/ask",
            headers=alice_h,
            json={"question": "배포 후 롤백은 어떤 기준으로 하나요?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["searched_documents"] == 1, body
        assert body["citations"], body
        answer = body["answer"]
        assert "2%" in answer or "10분" in answer, answer
        cite = body["citations"][0]
        assert cite["file_id"] == fid and cite["line_num"] >= 1, cite
        _ok(f"질의 성공 — 근거 {len(body['citations'])}건, 답변: {answer[:70]}")
        _ok(f"근거 앵커 — {cite['file_name']} · {cite['node_title']} · {cite['line_num']}줄")

        # 4. 자료에 없는 질문 — 존재를 부정하지 않는다
        r = await c.post(
            "/api/wiki/ask",
            headers=alice_h,
            json={"question": "사내 카페테리아 메뉴는 무엇인가요?"},
        )
        assert r.status_code == 200, r.text
        answer = r.json()["answer"]
        assert "없습니다" in answer, answer
        assert "그런 자료는 없" not in answer, answer
        _ok(f"자료 밖 질문 — 존재 부정 없이 응답: {answer[:60]}")

        # 2. 권한 필터가 대상 선정 단계에 — bob 은 검색조차 못 한다
        r = await c.post(
            "/api/wiki/ask",
            headers=bob_h,
            json={"question": "배포 후 롤백은 어떤 기준으로 하나요?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["searched_documents"] == 0, body
        assert not body["citations"], body
        assert "2%" not in body["answer"], body["answer"]
        _ok("권한 없는 사용자 — 검색 대상 0건, 본문이 컨텍스트에 들어가지 않음")

        # 3. 전사 공개 후에는 같은 질문에 답한다
        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"public": True}
        )
        assert r.status_code == 200, r.text
        r = await c.post(
            "/api/wiki/ask",
            headers=bob_h,
            json={"question": "장애 등급 P1 은 어떻게 알리나요?"},
        )
        body = r.json()
        assert body["searched_documents"] == 1, body
        assert body["citations"], body
        assert "전사" in body["answer"] or "공지" in body["answer"], body["answer"]
        _ok(f"전사 공개 후 — bob 도 답변 획득: {body['answer'][:70]}")

    await engine.dispose()
    print("\n위키 질의 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
