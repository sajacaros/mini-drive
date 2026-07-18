"""통합 테스트 부트스트랩 공용 헬퍼 — create_all 후 alembic_version 동기화.

통합 테스트는 dev DB 를 `Base.metadata.create_all` 로 out-of-band 재구성하지만
`alembic_version` 은 갱신하지 않는다. 그 결과 스키마는 head 인데 버전은 옛 리비전에 머무는
불일치가 남고, 이후 backend 기동 시 엔트리포인트의 `alembic upgrade head` 가 이미 존재하는
객체를 다시 만들려다 깨지는 것이 근본 원인이다(마이그레이션 존재 가드는 그 증상 방어).

여기서는 create_all 직후 head 로 스탬프해 "스키마 == alembic_version" 불변식을 원천에서
지킨다. head 리비전은 마이그레이션 스크립트에서 동적으로 읽어 하드코딩하지 않는다.
"""

from __future__ import annotations

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection


def stamp_alembic_head(sync_conn: Connection) -> None:
    """create_all 로 재료화된 스키마에 맞춰 `alembic_version` 을 head 로 스탬프한다.

    `AsyncConnection.run_sync(stamp_alembic_head)` 로 호출한다. `alembic stamp head` 와
    동일하게 동작한다 — `alembic_version` 테이블이 없으면 만들고 현재 리비전을 head 로 설정.
    """
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    MigrationContext.configure(sync_conn).stamp(script, "head")
