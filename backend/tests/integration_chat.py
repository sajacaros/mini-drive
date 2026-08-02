"""대화형 질의 통합 검증. **실제 사내 vLLM 을 호출한다.**

검증 축:
  1. 세션 CRUD — 생성·목록(최근순)·제목 변경·소프트 삭제.
  2. 남의 세션은 **404** 다(403 이 아니다 — 번호로 존재를 알아낼 수 없어야 한다).
  3. 답변에 근거가 붙고, 앵커가 파일·노드·줄 번호를 가리킨다.
  4. **후속 질문이 맥락을 잇는다** — "그럼 P2 는?" 처럼 주어가 빠진 질문이 통해야 한다.
     이 축이 이 파일의 존재 이유다. 툴 설명이 모델에게 "독립형 검색 질의를 만들라"고 시키는데,
     그게 실제 모델에서 통하는지는 대본으로는 확인할 수 없다.
  5. 견주는 질문이 **비교표 아티팩트**로 돌아온다(kind='comparison').
  6. 질문·답변은 한 트랜잭션이다 — 대화에 답 없는 질문만 남는 상태가 없다.

그래프 분기(왕복 상한·폴백·아티팩트 검증)는 모델 없이 `tests/test_chat_agent_unit.py` 가 본다.
여기서는 **실제 모델이 툴을 부르는가**를 본다 — 사내 vLLM 에 `--enable-auto-tool-choice
--tool-call-parser solar_open2` 가 켜져 있지 않으면 이 파일이 먼저 깨진다.

주의: `_reset()` 이 DB 와 Redis 를 통째로 지운다. 라이브 스택이 아니라 격리된 스택에서 돌릴 것.

사내망 밖에서는 vLLM 포트가 막혀 있고 SSH 만 열려 있다. 터널을 열고 base_url 만 바꿔 주면
그대로 돌아간다(호스트·포트·계정은 사내 문서를 따를 것 — 저장소에 적지 않는다):

    ssh -p <SSH포트> -N -L 17900:127.0.0.1:<vLLM포트> <계정>@<호스트> &
    WIKI_LLM_BASE_URL=http://127.0.0.1:17900/v1 python tests/integration_chat.py
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

DOC = """# 장애 대응 지침

이 문서는 장애 등급과 대응 절차를 정리한다.

## 장애 등급

P1 은 전체 사용자 영향, P2 는 일부 기능 불가, P3 는 우회 가능한 불편으로 구분한다.
P1 은 즉시 전사 공지하고, P2 는 담당 팀 채널에 알린다. P3 는 다음 주간 회의에서 공유한다.

## 대응 시한

P1 은 접수 후 15분 안에 1차 대응을 시작한다. P2 는 2시간, P3 는 3영업일 안에 처리한다.
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
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=300
    ) as c:
        alice_h, code = await setup_admin(c)
        bob_h, _ = await register_active(c, code, BOB)

        r = await c.post("/api/files", headers=alice_h, json={"name": "운영"})
        folder = r.json()["id"]
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("incident.md", DOC.encode(), "text/markdown")},
            data={"parent_id": str(folder)},
        )
        fid = r.json()["id"]
        r = await c.put(f"/api/files/{fid}/wiki", headers=alice_h, json={"enabled": True})
        assert r.status_code == 200, r.text
        counts = await _drain()
        assert counts.get("ready") == 1, counts
        _ok("문서 인덱싱 완료")

        # 1. 세션 생성 — 제목은 비어 있고 첫 질문에서 채워진다
        r = await c.post("/api/chat/sessions", headers=alice_h, json={})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert r.json()["title"] == "" and r.json()["last_message_at"] is None
        _ok(f"세션 생성 — id={sid}, 제목 미정")

        # 2. 남의 세션은 404 (403 이 아니다)
        r = await c.get(f"/api/chat/sessions/{sid}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.post(
            f"/api/chat/sessions/{sid}/messages",
            headers=bob_h,
            json={"question": "P1 은 어떻게 알리나요?"},
        )
        assert r.status_code == 404, r.text
        _ok("남의 세션 — 조회·질문 모두 404 (존재를 노출하지 않음)")

        # 3. 첫 질문 — 근거가 붙고 제목이 생긴다
        r = await c.post(
            f"/api/chat/sessions/{sid}/messages",
            headers=alice_h,
            json={"question": "P1 장애는 어떻게 알리나요?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        answer = body["answer"]
        assert answer["artifact"] is not None, answer
        assert answer["citations"], answer
        assert "전사" in answer["content"] or "공지" in answer["content"], answer["content"]
        cite = answer["citations"][0]
        assert cite["file_id"] == fid and cite["line_num"] >= 1, cite
        # 모델이 검색을 실제로 불렀는가 — tool calling 이 꺼져 있으면 여기서 깨진다.
        assert answer["tool_trace"], "모델이 search_wiki 를 부르지 않았다 (tool calling 확인)"
        _ok(f"첫 질문 — 검색 {len(answer['tool_trace'])}회, 근거 {len(answer['citations'])}건")
        _ok(f"근거 앵커 — {cite['file_name']} · {cite['node_title']} · {cite['line_num']}줄")

        r = await c.get(f"/api/chat/sessions/{sid}", headers=alice_h)
        detail = r.json()
        assert detail["title"], detail
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        _ok(f"첫 질문에서 제목 생성 — “{detail['title']}”")

        # 4. **후속 질문이 맥락을 잇는다** — 주어가 없다
        r = await c.post(
            f"/api/chat/sessions/{sid}/messages",
            headers=alice_h,
            json={"question": "그럼 P2 는?"},
        )
        assert r.status_code == 200, r.text
        follow = r.json()["answer"]
        text = follow["content"]
        assert "팀" in text or "채널" in text or "2시간" in text, text
        # 모델이 대화 맥락을 독립형 검색 질의로 바꿨는지 — 툴 흔적에 남는다.
        queries = " ".join(t["query"] for t in follow["tool_trace"])
        assert "P2" in queries or "장애" in queries, queries
        _ok(f"후속 질문이 맥락을 이음 — 검색 질의: {queries[:60]}")

        # 5. 견주는 질문 → 비교표 아티팩트
        r = await c.post(
            f"/api/chat/sessions/{sid}/messages",
            headers=alice_h,
            json={"question": "P1, P2, P3 의 대응 시한을 표로 비교해 주세요."},
        )
        assert r.status_code == 200, r.text
        art = r.json()["answer"]["artifact"]
        assert art["kind"] == "comparison", art
        assert len(art["columns"]) >= 2 and len(art["rows"]) >= 2, art
        assert all(len(row) == len(art["columns"]) for row in art["rows"]), art
        _ok(f"비교 질의 → 표 {len(art['columns'])}열 × {len(art['rows'])}행")

        # 6. 목록 — 대화한 세션이 위로 온다
        r = await c.post("/api/chat/sessions", headers=alice_h, json={})
        empty_sid = r.json()["id"]
        r = await c.get("/api/chat/sessions", headers=alice_h)
        items = r.json()["items"]
        assert r.json()["total"] == 2
        # 아직 대화 없는 세션이 맨 앞(방금 만든 것을 못 찾으면 안 된다), 그다음이 최근 대화.
        assert items[0]["id"] == empty_sid, items
        assert items[1]["id"] == sid and items[1]["last_message_at"], items
        _ok("목록 — 새 세션이 맨 앞, 대화한 세션이 그다음")

        # 7. 제목 변경 · 소프트 삭제
        r = await c.patch(
            f"/api/chat/sessions/{sid}", headers=alice_h, json={"title": "장애 대응 정리"}
        )
        assert r.status_code == 200 and r.json()["title"] == "장애 대응 정리", r.text
        r = await c.delete(f"/api/chat/sessions/{empty_sid}", headers=alice_h)
        assert r.status_code == 204, r.text
        r = await c.get("/api/chat/sessions", headers=alice_h)
        assert r.json()["total"] == 1, r.json()
        # 소프트 삭제라 메시지는 남아 있지만 열리지 않는다.
        r = await c.get(f"/api/chat/sessions/{empty_sid}", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("제목 변경 · 소프트 삭제 — 목록에서 사라지고 404")

        # 8. 빈 질문은 422 이고 대화에 아무것도 남지 않는다
        before = len((await c.get(f"/api/chat/sessions/{sid}", headers=alice_h)).json()["messages"])
        r = await c.post(
            f"/api/chat/sessions/{sid}/messages", headers=alice_h, json={"question": "   "}
        )
        assert r.status_code == 422, r.text
        after = len((await c.get(f"/api/chat/sessions/{sid}", headers=alice_h)).json()["messages"])
        assert before == after, (before, after)
        _ok("빈 질문 — 422, 대화에 잔해가 남지 않음")

    await engine.dispose()
    print("\n대화형 질의 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
