"""정합 스윕 단위 테스트 (DB/Redis/LLM 불필요) — spec/wiki-index.md 「정합 스윕」.

스윕은 유효 상태를 재도출해 저장 상태를 덮어쓴다. 판정이 틀리면 두 방향 다 나쁘다 —
멀쩡한 문서를 매일 재색인하거나(GPU 낭비), 켜져 있는 문서를 질의에서 통째로 내린다.
그래서 판정부(`plan_reconcile`)를 IO 와 갈라 두고 여기서 고정한다.

축 넷:

- 다섯 가지 어긋남을 각각 잡는가 (`ready` 인데 낡음 / 행 없음 / 큐 유실 / `failed` / 유효 OFF).
- 정상 상태에서 **아무 일도 하지 않는가**. 스윕이 매 회차 뭔가를 건드리면 그게 곧 재색인이다.
- 재시도 백오프 — 상한과 간격, 그리고 "새 버전이면 상한을 무시한다".
- 대량 내리기 가드 — 도출이 잘못됐을 때 한 회차에 전부 내리지 않는가.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.wiki import WikiStatus
from app.services.wiki_indexer import ReconcilePlan, StoredDoc, plan_reconcile

NOW = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)


def _doc(
    file_id: int,
    status: str,
    *,
    version: int = 1,
    attempts: int = 0,
    last_attempt_at: datetime | None = None,
) -> StoredDoc:
    return StoredDoc(
        file_id=file_id,
        status=status,
        version=version,
        attempts=attempts,
        last_attempt_at=last_attempt_at,
    )


def _plan(
    targets: dict[int, int],
    stored: list[StoredDoc],
    *,
    max_attempts: int = 5,
    retry_hours: int = 6,
) -> ReconcilePlan:
    return plan_reconcile(
        targets=targets,
        stored=stored,
        now=NOW,
        max_attempts=max_attempts,
        retry_hours=retry_hours,
    )


class TestNoDrift:
    def test_steady_state_touches_nothing(self) -> None:
        # 스윕의 기본 상태다. 여기서 뭔가가 잡히면 매일 전 문서를 다시 굽는다.
        plan = _plan({1: 3, 2: 1}, [_doc(1, WikiStatus.READY, version=3),
                                    _doc(2, WikiStatus.READY, version=1)])
        assert not plan.to_pending and not plan.to_stale
        assert not plan.to_requeue and not plan.to_disable

    def test_already_disabled_row_is_left_alone(self) -> None:
        # 꺼진 문서는 유예 동안 disabled 로 남는다 — 다시 내릴 것이 없다.
        plan = _plan({}, [_doc(9, WikiStatus.DISABLED)])
        assert not plan.to_disable


class TestOutdatedTree:
    def test_ready_but_behind_current_version_is_restaled(self) -> None:
        # 구멍 ① — 카탈로그가 "ready"라고 말하는데 트리는 몇 버전 전. 사용자가 알 방법이 없다.
        plan = _plan({1: 4}, [_doc(1, WikiStatus.READY, version=2)])
        assert plan.to_stale == [1] and plan.outdated == 1

    def test_ready_at_current_version_is_not_touched(self) -> None:
        plan = _plan({1: 4}, [_doc(1, WikiStatus.READY, version=4)])
        assert not plan.to_stale


class TestMissingRow:
    def test_enabled_file_without_row_is_created(self) -> None:
        # 구멍 ② — 행이 없으면 카탈로그에도 큐에도 없어 관측 자체가 불가능하다.
        plan = _plan({7: 1}, [])
        assert plan.to_pending == [7] and plan.missing == 1

    def test_disabled_row_that_is_enabled_again_is_revived(self) -> None:
        plan = _plan({7: 1}, [_doc(7, WikiStatus.DISABLED)])
        assert plan.to_pending == [7] and plan.revived == 1


class TestStuckInQueue:
    def test_pending_indexing_stale_are_requeued(self) -> None:
        # 구멍 ③ — Redis flush·워커 크래시의 흔적. 상태는 그대로 두고 큐에만 다시 넣는다.
        stored = [
            _doc(1, WikiStatus.PENDING),
            _doc(2, WikiStatus.INDEXING),
            _doc(3, WikiStatus.STALE),
        ]
        plan = _plan({1: 1, 2: 1, 3: 1}, stored)
        assert plan.to_requeue == [1, 2, 3] and plan.stuck == 3
        assert not plan.to_stale, "상태를 건드리지 않아야 한다"


class TestFailedRetry:
    def test_first_failure_is_retried(self) -> None:
        # 구멍 ④ — 재시도 경로가 없어 LLM 일시 장애 한 번이 영구 실패로 굳던 자리다.
        plan = _plan({1: 1}, [_doc(1, WikiStatus.FAILED, attempts=1,
                                   last_attempt_at=NOW - timedelta(hours=12))])
        assert plan.to_requeue == [1] and plan.retried == 1

    def test_status_is_not_promoted_to_stale(self) -> None:
        # 트리가 없는 실패 행을 stale 로 올리면 카탈로그가 "트리는 있으나 낡음"을 표시한다.
        plan = _plan({1: 1}, [_doc(1, WikiStatus.FAILED)])
        assert plan.to_requeue == [1] and not plan.to_stale and not plan.to_pending

    def test_retry_respects_max_attempts(self) -> None:
        # 상한 초과 파일처럼 영구 실패가 확정된 문서를 매일 굽지 않는다.
        plan = _plan(
            {1: 1},
            [_doc(1, WikiStatus.FAILED, attempts=5,
                  last_attempt_at=NOW - timedelta(days=30))],
        )
        assert not plan.to_requeue and plan.exhausted == 1

    def test_retry_respects_interval(self) -> None:
        # 스윕을 손으로 몇 번 돌리는 것만으로 시도가 소진되면 안 된다.
        plan = _plan(
            {1: 1},
            [_doc(1, WikiStatus.FAILED, attempts=1,
                  last_attempt_at=NOW - timedelta(minutes=30))],
        )
        assert not plan.to_requeue and plan.exhausted == 1

    def test_new_version_retries_regardless_of_attempts(self) -> None:
        # 입력이 바뀌었다 — 지난번에 죽은 이유가 그 파일에 있었다면 새 파일에서는 다를 수 있다.
        # 이게 없으면 사용자가 고쳐 올려도 영영 실패로 남는다.
        plan = _plan(
            {1: 9},
            [_doc(1, WikiStatus.FAILED, version=1, attempts=99,
                  last_attempt_at=NOW)],
        )
        assert plan.to_requeue == [1] and plan.retried == 1

    def test_max_attempts_zero_disables_auto_retry(self) -> None:
        plan = _plan({1: 1}, [_doc(1, WikiStatus.FAILED)], max_attempts=0)
        assert not plan.to_requeue and plan.exhausted == 1


class TestReverseDirection:
    def test_queryable_row_without_target_is_disabled(self) -> None:
        # 구멍 ⑤ — 불변식(인덱싱 ⟺ @전사 read)의 반대 방향. 회수된 문서로 계속 답하게 된다.
        plan = _plan({}, [_doc(1, WikiStatus.READY), _doc(2, WikiStatus.STALE)])
        assert plan.to_disable == [1, 2]

    def test_pending_row_without_target_is_also_disabled(self) -> None:
        # 대상 밖이 된 파일(확장자 변경 등)이 pending 으로 남으면 워커가 영영 집었다 놓는다.
        plan = _plan({}, [_doc(1, WikiStatus.PENDING)])
        assert plan.to_disable == [1]


class TestDisableGuard:
    def test_mass_disable_is_held_back(self) -> None:
        # 도출 질의가 잘못되면 한 회차에 전 문서가 질의에서 빠진다. 그건 사람이 봐야 한다.
        stored = [_doc(i, WikiStatus.READY) for i in range(100)]
        plan = _plan({}, stored)
        assert not plan.to_disable and plan.disable_skipped == 100

    def test_small_batches_pass_the_guard(self) -> None:
        # 문서가 몇 건뿐인 배포에서 정상 조작이 늘 가드에 걸리면 스윕이 무용지물이 된다.
        stored = [_doc(i, WikiStatus.READY) for i in range(4)]
        plan = _plan({2: 1, 3: 1}, stored)
        assert plan.to_disable == [0, 1] and not plan.disable_skipped

    def test_guard_allows_large_partial_disable(self) -> None:
        # 100건 중 30건을 내리는 것은 정상 조작 범위다 (폴더 하나를 껐는데 훅이 샌 경우).
        stored = [_doc(i, WikiStatus.READY) for i in range(100)]
        targets = {i: 1 for i in range(30, 100)}
        plan = _plan(targets, stored)
        assert len(plan.to_disable) == 30 and not plan.disable_skipped
