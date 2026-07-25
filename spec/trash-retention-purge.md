# 휴지통 보존 기간 — 자동 영구 삭제

작성: 2026-07-25. 사용자 결정:

- lazy 정리(요청에 얹기) 대신 **compose 사이드카 컨테이너가 하루 1회 고정 시각(KST)에 정리**.
- 사이드카가 검사만 하고 삭제는 backend 에 HTTP 로 위임하는 안은 검토 후 **기각**(아래).
- 정리 결과를 **Redis pub/sub 이벤트로 발행**해 열려 있는 휴지통 화면에 반영.

`soft_delete` 는 `deleted_at` 에 삭제 시각을 기록하지만(`services/files.py:1231`),
**저장소 전체에서 이 값을 읽어 정리하는 코드가 없다.** 결과는 두 가지다.

- 휴지통 항목이 `storage_used` 를 계속 점유한다. 회수는 사용자가 휴지통에서 영구 삭제를
  직접 누를 때만 일어난다(`routes/files.py:827`).
- 휴지통이 무한히 쌓이고, 언제 비워지는지 알려주는 표시도 없다.

## 왜 lazy 정리가 아닌가

`uploads.py` 의 `cleanup_expired`(`services/uploads.py:124`)는 새 재개 업로드 세션을
시작할 때만 호출된다(`:213`, `:270`). 만료 세션은 소량이고 정리 대상이 DB 행 + multipart
abort 뿐이라 그 방식이 맞다. 휴지통은 다르다.

- 실행 시각을 예측할 수 없다. **되돌릴 수 없는 삭제**를 아무 시간에 돌리게 된다.
- 정리 비용이 하필 그 순간 업로드하는 사용자의 응답 시간에 붙는다. 대량이 쌓였으면 더 심하다.
- 트래픽이 없으면 아예 돌지 않는다. 용량 회수가 목적인데 회수가 안 된다.

## 왜 Temporal 이 아닌가

이 스택엔 큐·스케줄러 의존성이 하나도 없다(celery·arq·apscheduler 전부 0건). Temporal
자가 호스팅은 서버 + 전용 DB 스키마 + 워커 + UI 를 더하고 백업 대상도 늘린다.

무게 문제만이 아니라 **작업의 모양이 안 맞는다.** Temporal 은 대상마다 오래 사는 타이머를
거는 데 강하고, 그러면 "삭제된 파일 1건 = 워크플로 1개"가 된다. 보존 기간을 30일 → 14일로
바꾸는 순간 돌고 있는 워크플로 전부를 취소·수정해야 한다. `WHERE deleted_at < now() -
interval` 는 매 실행에 현재 설정을 다시 읽는다. 설정 변경 즉시 반영이 이 기능에선 정상 동작이다.

## 왜 호스트 cron 이 아닌가

`backup.sh` 선례가 있지만(README:186), 배포 형태가 로컬과 `deploy/` 두 가지다. 호스트마다
crontab 을 따로 등록해야 하고 빠뜨리면 **에러 없이 그냥 안 돈다.** compose 에 선언하면
로컬·prod·deploy 어디서든 스택과 함께 따라간다.

## 왜 HTTP 위임이 아닌가

사이드카가 기한 초과 항목을 **검사만** 하고 실제 삭제는 backend 에 요청하는 안을 검토했다.
얻는 것은 "데이터를 지우는 프로세스가 하나뿐"이라는 성질이고, 그건 진짜 이점이다. 다만
그 성질을 가장 비싸게 사는 방법이다.

- **서비스 간 인증이 없다.** 이 앱의 인증은 사용자 JWT 뿐이다. 대량 삭제 권한을 가진 장기
  토큰을 새로 만들면 compose 3개와 `deploy/.env` 에 전파할 비밀이 하나 늘고, 인증 없는
  내부 엔드포인트로 두면 backend 가 `expose: 8000` 으로 도커 네트워크에 열려 있어
  (`docker-compose.yml:27`) **네트워크의 아무 컨테이너나 인증 없이 대량 삭제를 트리거**할 수
  있게 된다. 지금은 그런 엔드포인트가 존재하지 않는다.
- **타임아웃·재시도 의미가 생긴다.** 대량 정리를 위임하면 요청 하나가 수 분을 돈다. 타임아웃
  시 호출자는 완료 여부를 모르고, 재시도하면 할당량 이중 차감 위험이 돌아온다(아래 4절).
  결국 페이지네이션 + 멱등 키를 사이드카가 직접 구현하게 된다.
- **잠금은 사라지지 않고 이동한다.** 수동 실행도 같은 엔드포인트를 타면 동시 호출이 가능하다.
- **삭제 로직 중복은 이미 해결돼 있다.** 아래 1절의 `purge_tree` 를 사용자 경로와 사이드카가
  함께 호출하므로 로직은 한 군데뿐이다. 위임이 개선하는 것은 코드 위치가 아니다.
- **정리 비용이 웹 프로세스로 돌아온다.** lazy 정리를 버린 이유가 그것이었다.

목표가 "단일 뮤테이터"라면 HTTP 위임보다 backend `lifespan` 내부 스케줄러가 모든 면에서
싸다(같은 이점, 엔드포인트·비밀·프로토콜 없음). 그 축과 "배치 삭제가 요청 처리 프로세스를
건드리지 않는다" 축 사이에서 후자를 택해 사이드카로 확정했다.

---

## 설정 (`core/config.py`)

```python
# 휴지통 보존 기간. 기본 7일로 자동 정리가 켜져 있고, 끄려면 0 을 명시한다.
# purge_hour 는 KST 기준 실행 시각 — 되돌릴 수 없는 삭제라 사람이 안 쓰는 시간에 돈다.
trash_retention_days: int = 7
trash_purge_hour: int = 4
trash_purge_batch: int = 200
# 회차당 개별 SSE 이벤트 상한. 초과분은 소유자별 요약 1건으로 접는다.
trash_purge_event_cap: int = 200
```

**기본값은 0 → 7 로 뒤집혔다 (2026-07-26, 사용자 결정).** 최초 설계는 0 이었다. 근거는
기존 배포에 `deleted_at` 이 오래 전부터 쌓여 있어 7 을 기본값으로 두면 업데이트 직후 첫
실행이 수만 건을 한꺼번에 지운다는 것이었고, 그 위험 자체는 지금도 그대로다.

바뀐 것은 그 위험을 어디서 막느냐다. 0 기본값은 **아무도 켜지 않으면 기능이 없는 것과 같다** —
용량 회수가 목적인데 운영자가 `.env` 를 손대야만 시작되고, 안 하면 조용히 아무 일도 안 일어난다.
7 기본값은 신규·기존 배포 모두에서 기능이 실제로 동작하는 대신, **업그레이드 경로에 문서화된
경고 하나**를 요구한다(README 운영 절 + `.env.example` + compose 주석). 끄는 것은 여전히
`TRASH_RETENTION_DAYS=0` 한 줄이고, `--dry-run` 으로 규모를 먼저 보는 절차도 그대로다.

기존 배포를 올리는 운영자에게는 이 변경이 **동작 변화**다. 릴리스 노트에 반드시 싣는다.

**마이그레이션은 없다.** `deleted_at` 은 이미 있고(`models/file.py:77`), 설정은 환경변수고,
UI 표시는 파생 필드다. 새 테이블·컬럼이 없다.

---

## 백엔드

### 1. `permanent_delete` 를 인가와 분리 (`services/files.py`)

현재 `permanent_delete`(`:1327`)는 첫 줄에서 행위자 인가를 한다.

```python
file = await ensure_file_access(session, user, await get_file(session, file_id), need="manage")
```

자동 정리에는 **행위자가 없다.** 시스템 사용자를 만들거나 super_admin 을 빌려 쓰면 인가
모델이 오염된다. 대신 함수 본문(`:1339~1395`)을 그대로 떼어낸다.

```python
async def purge_tree(session, storage, file: File) -> tuple[int, int]:
    """휴지통 항목 하나와 하위 전체를 영구 삭제하고 purge 이벤트를 발행한다.
    인가는 호출자 책임. 반환: (삭제한 files 행 수, 회수한 바이트 합계)."""
```

`permanent_delete` 는 인가 + `is_deleted` 검사만 남기고 `purge_tree` 를 호출한다. 이렇게
해야 소유자별 할당량 회수(`614ecdf` 에서 고친 문제 — `:1357` 주석), `shares` 선삭제
(`:1380`), 썸네일 키 제거(`:1355`), best-effort 오브젝트 삭제(`:1393`)가 자동 정리에도
그대로 적용된다. **생 SQL `DELETE` 를 새로 쓰면 그 네 가지를 전부 다시 틀린다.**

### 2. 정리 서비스 (`services/trash.py`, 신규)

```python
async def purge_expired(session, storage, *, dry_run: bool = False) -> PurgeResult
```

대상 선정은 **삭제 루트만** 고른다 — `list_trash`(`:1246`)의 판정을 그대로 쓴다. 부모가
살아 있거나 없는 삭제 항목이 삭제 루트고, 폴더 재귀 삭제로 함께 지워진 하위는 부모도 삭제
상태라 제외된다. 여기에 기한 조건을 더한다.

```sql
File.is_deleted IS TRUE
AND File.deleted_at < now() - make_interval(days => :retention)
AND (parent.id IS NULL OR parent.is_deleted IS FALSE)
ORDER BY File.deleted_at        -- 오래된 것부터
LIMIT :batch
```

하위 항목을 따로 고르지 않아도 되는 이유: 하위는 `purge_tree` 의 subtree CTE
(`:1209`)가 함께 지운다. 하위의 `deleted_at` 이 루트보다 이를 수 있지만(살아 있는 폴더 안의
파일을 먼저 지웠고 나중에 폴더를 지운 경우) 그때 사용자가 휴지통에서 보는 항목은 폴더 하나뿐이라
폴더 기준으로 함께 사라지는 게 화면과 일치한다.

### 3. 배치 상한은 트랜잭션 크기 제한이지 하루 처리량 제한이 아니다

`trash_purge_batch=200` 을 "한 회차에 200건"으로 해석하면, 누가 5만 건을 버렸을 때 하루
200건씩 **250일** 걸린다. 그동안 용량은 계속 잡혀 있다. 한 회차 안에서 **배치가 상한보다
적게 돌아올 때까지 반복**하고, 다 비면 다음 실행 창까지 잔다.

```python
while True:
    n = await _purge_batch(...)          # 최대 trash_purge_batch 건
    total += n
    if n < settings.trash_purge_batch:
        break
```

### 4. 중복 실행 잠금 (Redis)

사이드카는 한 대지만, 운영자가 그 와중에 `docker compose exec backend python -m app.cli
purge-trash --once` 를 돌릴 수 있다. 이때 같은 항목을 두 프로세스가 집으면 양쪽이 크기를
읽고 양쪽이 `_release_quota`(`:340`)를 호출해 **할당량이 이중 차감**된다(`GREATEST(0, …)`
바닥에 걸려 조용히 어긋난다). DB DELETE 는 한쪽만 성공하므로 파일은 안전하지만 사용량 수치가
망가진다.

정리 회차 전체를 Redis 리스로 감싼다 — `SET trash:purge:lock <id> NX EX 3600`. 이미
Redis 를 쓰고 있고(`core/redis.py`, rate limiting) 10 줄이면 이 문제군이 사라진다. 잠금을
못 잡으면 조용히 건너뛴다(다음 회차에 다시 온다).

### 5. CLI (`app/cli.py`)

`create-admin` 과 같은 argparse 서브커맨드로 추가한다(`cli.py:45`).

| 플래그 | 동작 |
|---|---|
| `--once` | 한 회차만 실행하고 종료. 수동 실행·검증용 |
| `--dry-run` | 대상 건수와 회수될 바이트만 출력. 아무것도 지우지 않는다 |
| `--loop` | 다음 실행 창까지 자며 무한 반복. 사이드카가 쓰는 모드 |

`trash_retention_days == 0` 이면 `--loop` 은 그 사실을 한 번 로그로 남기고 유휴 대기한다.
**종료하면 안 된다** — `restart: unless-stopped` 가 종료 코드와 무관하게 재시작해 로그가
재시작으로 도배된다.

### 6. 다음 실행 시각 (`--loop`)

간격 기반 `sleep(86400)` 은 **컨테이너 재시작 시각에 실행 시각이 끌려다닌다.** 오후 2시에
배포하면 그 뒤로 매일 오후 2시에 파괴적 삭제가 돈다. 벽시계 목표로 계산한다.

```python
_KST = ZoneInfo("Asia/Seoul")   # todos.py:31, archives.py:34 와 동일

def next_run_at(now: datetime) -> datetime:
    """다음 purge_hour(KST). 재시작해도 실행 시각이 고정된다."""
    target = now.astimezone(_KST).replace(
        hour=settings.trash_purge_hour, minute=0, second=0, microsecond=0
    )
    return target if target > now else target + timedelta(days=1)
```

**잠들기를 먼저 한다.** 기동 직후에 돌지 않는다 — 부수 효과로 신규 설치의 첫 부팅에서
`alembic upgrade head` 와 경합하지 않는다(사이드카는 entrypoint 를 우회하므로 마이그레이션을
직접 돌리지 않는다. 아래 compose 참조).

---

## 실시간 반영 (SSE)

발행은 Redis pub/sub 로 한다 — 이미 이 프로젝트의 이벤트 버스다. `publish_file_event`
(`services/file_events.py:62`)를 그대로 쓰므로 **새 인프라가 없고, 사이드카에서 발행해도
동작한다**(`:31` "단일 pub/sub 채널 — 프로세스/워커 수와 무관하게 동작한다").

### 그냥 발행하면 전부 버려진다

구독자 권한 필터 `user_can_receive_event`(`:89`)는 이벤트마다 `session.get(File, file_id)`
로 행을 찾아 판정한다. **영구 삭제는 행이 사라진다** → `:101` 에서 `None` → `False` →
소유자에게조차 전달되지 않는다. docstring 이 그 한계를 이미 적어 두었다(`:93` "파일 행이
없으면(영구 삭제 등) 전달하지 않는다").

즉 삭제 후 발행은 필터를 함께 손대야 성립한다.

### purge 이벤트는 자기완결형으로

`type="purge"` 를 새로 넣고, 페이로드에 `owner_id` 를 실어 **행 조회 없이** 판정한다.
분기는 `file_id` 타입 가드(`:97`)보다 **먼저** 둔다 — 요약 이벤트에는 `file_id` 가 없다.

```python
# purge 는 행이 이미 없어 조회로 판정할 수 없다. 페이로드의 owner_id 로만 판정한다.
if event.get("type") == "purge":
    return event.get("owner_id") == user.id
```

**청중이 정확히 소유자 한 명인 것이 이 설계의 근거다.** 소프트 삭제 항목은 어떤 폴더 목록에도
나오지 않고, 협업자의 휴지통에도 나오지 않는다 — `list_trash`(`services/files.py:1246`)가
`user_id == user.id` + 부모 생존 조건으로 거른다. 폴더 하위에 남의 파일이 섞여 있어도
(`:1357` 주석) 그들은 그 항목을 본 적이 없다. 화면이 바뀌는 사람은 삭제 루트의 소유자뿐이다.

부하 면에서도 이쪽이 맞다. 현재 필터는 이벤트마다 짧은 DB 세션을 연다(`:99`). 배치 정리는
이벤트가 많을 수 있어 열린 SSE 연결 수만큼 곱해지는데, `owner_id` 비교는 **DB 조회 0회**다.

발행 시점은 **commit 이후**, 실패는 fail-open(`:71` 원칙 그대로) — 발행 실패가 이미 끝난
삭제를 되돌리지 않는다.

### Phase 8 미적용 화면 보완

이 절의 작업 절반은 새 기능이 아니라 **Phase 8 이 파일 브라우저에만 적용한 것을 휴지통까지
넓히는 것**이다. `drive-ux-phase8.md:15` 의 발행 지점 목록은 `업로드·새 버전·폴더 생성·이름
변경·이동·소프트 삭제·복원·권한 부여/회수` 이고 **영구 삭제가 없다.** 프론트 범위도 "파일
브라우저가 스트림을 구독한다"였다. 백로그 제외 목록(`:81`)에도 없으니 미룬 것이 아니라 범위
밖이었다.

그 결과 휴지통 화면에 지금 세 가지 결함이 있다. 두 탭을 열어 두면 재현된다.

| 다른 탭에서 한 일 | 발행 | 휴지통 반영 | 낡은 행을 누르면 |
|---|---|---|---|
| 휴지통에 버림 | O (`files.py:1237`) | X | — (나타나지 않음) |
| 복구 | O (`:1317`) | X | 409 (`:1275`) |
| 영구 삭제 | **X** | X | 404 (`:83`) |

**양쪽을 다 고쳐야 한다.** 백엔드만 고치면 아무도 듣지 않는 채널로 이벤트가 나가고, 프론트만
고쳐도 영구 삭제는 발행이 없어 그대로다. 그래서 발행(7번)과 구독(10번)이 짝이다.

발행을 `purge_tree` 에 두면 사용자의 수동 영구 삭제도 이벤트를 낸다 — **의도된 동작 변화**이므로
테스트에 넣는다.

### 폭주 상한

삭제 루트 단위로 발행하므로 하위 수만 개가 이벤트 하나로 접힌다. 그래도 루트가 수천 개인 첫
실행이 있으니 회차당 발행 수에 상한(`trash_purge_event_cap = 200`)을 두고, 초과하면 개별
발행을 멈추고 **소유자별 요약 1건**(`file_id` 없이 `purged` 개수)을 회차 끝에 보낸다.
`spec/folder-upload-batch.md:335` "SSE 이벤트 폭주" 절의 선례를 따른다.

프론트는 `file_id` 가 있으면 그 행만 제거하고, 없으면 목록을 재조회한다.

요약 이벤트 때문에 `publish_file_event` 의 `file_id`·`name` 을 옵셔널로 넓혀야 한다
(`:62` 시그니처). 기존 호출부는 전부 값을 주므로 영향이 없고, 필터는 위 분기가 타입 가드보다
앞서므로 안전하다.

---

## compose

### 이미지 재사용

`deploy/docker-compose.deploy.yml:46` 은 이미 `image: flex-drive-backend:latest` + `build`
조합을 쓴다. 루트 `docker-compose.yml` 의 backend 에도 같은 태그를 붙이고, 사이드카는
`build` 없이 그 태그만 참조한다. 이미지가 하나라 빌드 시간·디스크 추가 비용이 없다.

### 환경변수 중복 제거

backend 의 environment 블록은 13 줄이다(`docker-compose.yml:29~44`). 그대로 복사하면
compose 파일 3개에 39 줄이 늘고, 앞으로 환경변수 하나를 추가할 때 6곳을 고쳐야 한다.
YAML 앵커로 묶는다 — `deploy/docker-compose.deploy.yml:16` 의 `x-logging: &default-logging`
이 이미 같은 관용구를 쓴다.

```yaml
x-backend-env: &backend-env
  DATABASE_URL: postgresql+asyncpg://...
  # … 기존 13줄 그대로

services:
  backend:
    image: flex-drive-backend:latest
    build: { context: ./backend, dockerfile: Dockerfile }
    environment: *backend-env
    # …

  # ── 휴지통 자동 정리 (하루 1회, KST trash_purge_hour) ──
  purger:
    image: flex-drive-backend:latest
    # entrypoint 우회 — 마이그레이션은 backend 가 담당한다(중복 alembic 실행 방지).
    entrypoint: ["python", "-m", "app.cli", "purge-trash", "--loop"]
    environment: *backend-env
    depends_on:
      backend:
        condition: service_started
    networks: [minidrive]
```

`entrypoint` 를 덮는 게 핵심이다. 기본 entrypoint 는 `alembic upgrade head` 를 선행
실행하므로(`backend/docker-entrypoint.sh:7`) 그대로 두면 backend 와 동시에 마이그레이션을
돌려 경합한다.

prod 오버라이드에는 다른 서비스와 같은 규약을 적용한다 — `restart: unless-stopped`,
`logging`, 그리고 리소스 제한은 작게(`cpus: "0.5"`, `memory: 256M`). `deploy/` 에도 같은
블록을 넣는다.

---

## 프론트

`TrashPage.tsx` 에 이미 `삭제일` 컬럼이 있다(`:87`, `:105`). 그 옆에 **`자동 삭제`** 컬럼을
더한다.

값은 서버가 계산해 내려준다 — `FileResponse` 의 파생 필드 관용구를 따른다(`schemas/files.py:28`
"파생 필드 … 서비스가 File 인스턴스에 부착하면 채워지고, 없으면 …").

```python
purge_at: datetime | None = None    # 파생 필드. 보존 기간이 0 이면 None.
```

`list_trash` 가 `deleted_at + retention_days` 를 부착한다. 프론트에 보존 기간 설정값을
따로 내려줄 필요가 없고, 별도 config 엔드포인트도 만들지 않는다.

표시는 남은 일수 기준으로 — `3일 후 삭제`, 당일이면 `오늘 삭제`, `purge_at` 이 없으면 `—`.
남은 기간이 3일 이하면 경고색을 준다. 목록 상단에 안내 한 줄: `휴지통 항목은 삭제 후
7일이 지나면 자동으로 영구 삭제됩니다.` (보존 기간이 0 이면 문구를 숨긴다.)

일수는 서버 설정을 따로 내려받지 않고 **행에서 역산한다** — `purge_at - deleted_at`. 보존
기간을 프론트에 노출하는 새 엔드포인트나 `list_trash` 응답 형태 변경(현재 `list[FileResponse]`,
`routes/files.py:377`)이 필요 없다. 목록이 비어 있으면 문구도 필요 없다.

### SSE 구독 추가

**`TrashPage` 는 현재 SSE 를 구독하지 않는다.** 구독하는 페이지는 `FileBrowserPage` 하나뿐이다
(`lib/fileEvents.ts` 사용처). 위 "Phase 8 미적용 화면 보완" 의 세 결함이 여기서 해결된다 —
`subscribeFileEvents` 로 `purge`·`delete`·`restore` 를 받아 목록을 갱신하고(`purge` 는 해당
행 제거, 나머지는 재조회), `onReconnect` 에는 기존 관용구대로 전체 재조회를 붙인다
(`FileBrowserPage.tsx:337`).

`FileEvent` 인터페이스도 함께 넓힌다 — `file_id`·`name` 옵셔널, `owner_id` 추가
(`lib/fileEvents.ts:17` 이 "backend `publish_file_event` 와 1:1" 규약이다).

### `FileBrowserPage` 에 타입 가드 (필수)

현재 핸들러는 **이벤트 타입을 보지 않고** `parent_folder_id` 만 비교해 재조회한다
(`FileBrowserPage.tsx:335`). 삭제 루트의 부모는 **살아 있는 폴더**이므로(그게 삭제 루트
조건이다) purge 이벤트가 그 폴더를 보고 있는 사용자에게 무의미한 재조회를 일으킨다. 영구
삭제된 항목은 어차피 그 목록에 없다.

```ts
if (e.type === "purge") return;   // 휴지통에서만 의미 있는 이벤트
```

`parent_folder_id` 를 `null` 로 발행해 회피하는 방법은 쓰지 않는다 — 프론트 규약에서 `null`
은 개인 루트를 뜻해(`file_events.py:_normalize_container`) 루트에서 같은 문제가 난다.

---

## 관측

- **구조화 로깅**: 회차마다 한 줄 — `trash_purge_done` + `purged`, `bytes_reclaimed`,
  `duration_ms`. 항목마다도 한 줄(`file_id`, `name`, `owner_id`, `size`, `deleted_at`).
  하루 1회라 `docker compose logs purger` 가 그대로 읽힌다.
- **메트릭**(`core/metrics.py`): 기존 `minidrive_` 접두 규약을 따라
  `minidrive_trash_purged_total`, `minidrive_trash_purged_bytes_total` 카운터 2개.
- **`audit_logs` 에는 기록하지 않는다.** `actor_id` 가 `NOT NULL` + users FK 이고
  (`models/audit.py:23`), 테이블 자체가 "누가 이 링크를 껐나"에 답하는 **사람 행위** 기록이다
  (모델 docstring). 자동 정리에는 행위자가 없다. 억지로 넣으려면 컬럼을 nullable 로 바꾸고
  `AdminAuditPage` 표시도 고쳐야 하는데, 이 기능 하나로 감당할 변경이 아니다. → 미결 참조.

---

## 작업 순서

1. `config.py` 설정 4개 + `.env.example` 항목 추가
2. `permanent_delete` 에서 `purge_tree` 추출 — 이벤트 발행 전이므로 동작 변화 없음,
   기존 테스트로 회귀 확인
3. `services/trash.py` — 대상 선정 쿼리 + 드레인 루프 + Redis 잠금 + `--dry-run` 집계
4. `next_run_at` 순수 함수 + 유닛 테스트(경계: 실행 시각 직전/직후/정각)
5. `cli.py` `purge-trash` 서브커맨드 (`--once` / `--dry-run` / `--loop`)
6. 통합 테스트 — `deleted_at` 을 과거로 조작한 항목이 정리되고, 기한 내 항목은 남고,
   소유자가 다른 파일이 섞인 폴더에서 할당량이 소유자별로 회수되는지
7. **이벤트** — `publish_file_event` 시그니처 완화, 필터에 `purge` 분기,
   `purge_tree` 에서 발행, 회차 상한·요약 이벤트. 필터 분기 유닛 테스트(행이 없는 상태에서
   소유자만 통과하는지)와 수동 영구 삭제가 이제 이벤트를 내는지 확인
8. compose 3개 파일 — 앵커 + `image` 태그 + `purger` 서비스
9. `purge_at` 파생 필드 + `TrashPage` 컬럼·안내 문구
10. 프론트 이벤트 — `FileEvent` 타입 확장, `TrashPage` 구독, `FileBrowserPage` 타입 가드
11. README — 휴지통 보존 기간 절, `purger` 서비스, `--dry-run` 운영 절차

2번까지는 동작 변화가 없고, 8번을 넣기 전까지는 `--dry-run` 으로만 검증한다. 7번의 발행은
`purger` 없이도 수동 영구 삭제로 검증할 수 있다.

---

## 명시적으로 하지 않는 것

- **휴지통 용량 상한**(사용자당 N GB 넘으면 오래된 것부터) — 보존 기간과 목적이 겹치고,
  "왜 30일도 안 됐는데 사라졌나"를 설명하기 어렵다.
- **삭제 예고 알림** — 알림 인프라가 아직 없다. UI 카운트다운으로 갈음한다.
- **관리자 화면에서 보존 기간 편집** — 환경변수로 충분하다. `app_settings` 테이블에 넣으면
  사이드카가 DB 를 읽어야 하고 변경 시점 반영 규칙이 생긴다.
- **버전만 오래된 것 정리**(현재 버전은 두고 옛 버전만) — 별개 기능이고 할당량 모델
  (`:925` 주석 — `storage_used` == 모든 버전 크기 합계)을 따로 검토해야 한다.

## 미결

- **자동 삭제 이력을 어디에 남기나.** 지금 설계는 컨테이너 로그뿐이고, 로그는
  `max-size: 10m` × `max-file: 5` 로 로테이션된다(`deploy/docker-compose.deploy.yml:16`).
  "내 파일이 왜 없어졌나"에 몇 달 뒤 답해야 한다면 `audit_logs.actor_id` 를 nullable 로
  바꾸고 시스템 행위자를 표현하는 편이 정직하다. 다른 자동 작업(고아 세션 정리 등)도 같은
  칸을 쓰게 되므로, 그때 한꺼번에 하는 게 맞다고 판단해 이번 범위에서 뺐다.
- **`purger` 를 나중에 어디까지 쓸 것인가.** 썸네일 생성을 업로드 요청 경로에서 빼내는 일이
  백로그에 있다(`3bfd7e3` — 썸네일 실패가 업로드를 500 으로 무너뜨린 회귀). 그때 이 컨테이너를
  범용 워커로 넓힐지, 별도 큐(Redis 기반 arq)를 들일지는 그 시점에 정한다. 이번엔 이름과
  책임을 정리 하나로 좁혀 둔다.
