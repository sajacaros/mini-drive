# BeeMate — 폰에서 내 PC 로 파일 보내기

작성: 2026-11-21. 사용자 결정:

- 폰에서 찍은 사진·받은 파일을 **내 PC 로 바로** 보낸다. 지금은 디스코드·팀즈·슬랙 개인 채널에
  올렸다가 PC 에서 다시 받는다.
- 폰은 Flutter 앱. 보내는 길은 둘 — (A) 앱 안 최근 파일 그리드에서 오른쪽 벽으로 끌기,
  (B) OS 공유 메뉴 → BeeMate. 다른 앱 위 오버레이(화면 끝 띠)는 Kotlin 이 필요해 **제외**.
- PC 는 **아무것도 설치하지 않는다.** 드라이브 웹의 `/beemate` 탭이 수신기다.
  이미지 → 클립보드(+사본 저장), 그 외 → 사용자가 고른 BeeMate 폴더.
- 대상은 사내 구성원, 회사 밖에서도 쓴다. 파일이 회사 서버를 거쳐 가는 것은 허용.

## 왜 드라이브 파일(`files`)에 넣지 않는가

전송은 **거쳐 가는** 데이터다. 받는 순간 지워지고, 못 받아도 하루면 지운다. `files` 에 넣으면
쓰지 않을 기능이 전부 따라붙는다.

- 할당량 선점·회수(`_reserve_quota`) — 잠깐 머무는 사진이 드라이브 용량을 깎는다.
- 버전(`file_versions`), 위키 색인 토글, 최근 항목, 즐겨찾기, 휴지통.
- 파일 이벤트(`file-events`) — 열린 드라이브 화면마다 "새 파일" 이 떴다 사라진다.

그래서 테이블 둘(`beemate_devices`, `beemate_transfers`)과 오브젝트 접두어
`beemate/{user_id}/{transfer_id}` 를 따로 둔다. 저장소(SeaweedFS)·게이트웨이 다운로드
(X-Accel-Redirect)·인증은 그대로 쓴다.

## 모델

**기기** `beemate_devices`

| 컬럼 | |
|---|---|
| id, user_id(→users, CASCADE) | 기기는 계정에 속한다. 남의 기기는 보이지도 않는다 |
| client_id | 클라이언트가 만든 고유 id. (user_id, client_id) 유일 |
| name | 처음엔 "Windows · Chrome" 처럼 자동, 사용자가 바꾼다 |
| platform | `android` \| `ios` \| `web` |
| is_favorite | 켜져 있든 꺼져 있든 목록 맨 위 |
| is_hidden | 보낼 곳 목록에서 뺀다(관리 목록에는 남는다) |
| created_at, last_seen_at | last_seen_at 은 이벤트 스트림 연결 시 갱신 |

### 기기 — 자동 등록 (2026-11-21 변경)

등록 화면을 없앴다. 사용자가 할 일은 PC 에서 탭을 열어 두는 것뿐이다.

- **연결 자체를 기기로 쓰지 않는다.** SSE 연결은 새로고침·와이파이 끊김·절전 복귀마다 새로
  맺어진다. 연결 = 기기면 폰에서 고른 대상이 그 사이 사라지고, 끊긴 동안 온 파일을 받을 곳이
  없어진다. 그래서 클라이언트가 처음 붙을 때 만든 `client_id`(브라우저 localStorage, 앱
  설치별)를 매 연결에 싣고, 서버가 그것으로 기기를 찾거나 만든다(upsert).
- upsert 는 **이름을 덮어쓰지 않는다** — 사용자가 바꾼 이름을 다음 접속이 기본 이름으로
  되돌리면 안 된다.
- 같은 브라우저의 탭 여러 개는 같은 기기다. 그대로면 사진이 두 번 복사되므로 Web Locks 로
  **한 탭만** 받고, 그 탭을 닫으면 남은 탭이 이어받는다.
- 0023 시절에 만든 기기는 `legacy-{id}` client_id 로 채운다. 그 브라우저는 새 기기로 다시
  붙으므로 옛 행은 사용자가 지운다.

### 보낼 곳 목록

모든 기기를 보여 준다(숨김 제외). 순서는 **즐겨찾기 → 켜진 기기 → 나머지**, 같은 무리 안은
등록 순. 즐겨찾기는 꺼져 있어도 맨 위다 — 사용자가 고른 자리가 접속 상태로 흔들리면 안 된다.
꺼진 기기에도 보낼 수 있고, 그 기기는 다음에 켜질 때 당겨 간다.

**전송** `beemate_transfers`

| 컬럼 | |
|---|---|
| id, user_id(→users, CASCADE) | 보낸 사람 = 받는 사람. 1차는 **내 기기끼리만** |
| to_device_id(→devices, CASCADE) | 받을 기기 |
| from_device_id(→devices, SET NULL) | 보낸 기기(표시용). 지워져도 전송은 남는다 |
| name, mime_type, size, object_key | |
| created_at | 만료 판정 기준 |

상태 컬럼은 없다. **받으면 행을 지운다.** "받았지만 남아 있는" 상태를 두면 그걸 다시 지우는
경로가 하나 더 생긴다.

## 남에게 보내기를 왜 빼는가

1차 범위는 내 기기끼리다. 남에게 보내려면 수신 동의, 차단, 감사 로그, 스팸 상한이 같이 와야
하고, 그건 드라이브의 공유 링크가 이미 하는 일이다. 소유 판정이 `user_id` 하나로 끝나는 지금
구조가 가장 단순하다.

## 실시간 — 기기별 채널

`file-events` 는 채널 하나에 모든 이벤트를 싣고 구독자마다 권한을 판정한다. 전송은 받을 기기가
정해져 있으므로 **채널 자체를 기기별로 나눈다** (`beemate:events:{device_id}`). 구독 라우트가
기기 소유를 확인하는 것이 곧 권한 판정이라 이벤트마다 DB 를 볼 필요가 없다.

- **스트림에는 내용을 싣지 않는다.** 프레임은 `hello`(이 연결의 기기 id) 한 번, 이후
  `{"type":"pending"}` 신호뿐이다. 받는 쪽은 신호를 받으면 `GET /transfers?device_id=` 로
  당겨 간다(pull). 실시간 경로와 "꺼져 있다 켜졌을 때" 경로가 하나로 같아지고, 신호를 놓쳐도
  다음 신호나 재접속 때 빠짐없이 받는다.
- 접속 순서: 서버는 구독을 마친 **뒤에** hello 를 보내고, 클라이언트는 hello 를 받은 뒤 당긴다.
  반대 순서면 그 사이에 온 전송을 놓친다. 같은 전송이 두 번 당겨질 수 있어 id 로 거른다.
- 온라인 표시: 스트림이 살아 있는 동안 Redis `beemate:online:{device_id}` 를 keepalive 마다
  TTL 갱신한다. 끊기면 TTL(75초) 뒤 오프라인. 오프라인 기기에도 보낼 수 있다(탭을 열면 받는다).
- 발행은 fail-open — Redis 가 죽어도 업로드는 성공하고, 받는 쪽은 다음 접속 때 목록으로 받는다.

## 크기·횟수 상한

- 파일 1개 `BEEMATE_MAX_FILE_BYTES` 기본 500 MiB. 사진·문서가 대상이고 대용량은 드라이브가 맡는다.
- 사용자당 아직 안 받은 전송 합계 `BEEMATE_MAX_PENDING_BYTES` 기본 2 GiB. 꺼진 PC 로 계속
  보내 저장소를 채우는 것을 막는다.
- 사용자당 기기 `BEEMATE_MAX_DEVICES` 기본 20.
- 업로드 rate limit 은 드라이브 업로드(분당 10)와 **따로** 둔다(`beemate`, 분당 60). 사진 열
  장을 한 번에 끌면 드라이브 한도에 바로 걸린다.

## 정리

- 받으면(`DELETE /transfers/{id}`) 오브젝트와 행을 바로 지운다.
- 안 받은 전송은 `BEEMATE_TRANSFER_TTL_HOURS`(기본 24) 뒤 만료. 만료분은 목록·다운로드에서
  즉시 안 보이고(조회 조건), 실제 삭제는 두 곳에서 한다.
  - 새 전송을 만들 때 **그 사용자의** 만료분을 지운다 — 상한 계산이 만료분에 막히지 않게.
  - `purger` 회차가 전체 만료분을 지운다 — 다시 보내지 않는 사용자 몫.
- 기기를 지우면 그 기기로 가던 전송의 오브젝트를 먼저 지우고 행은 CASCADE 로 지운다.

## API (`/api/beemate`)

| | |
|---|---|
| `GET /events?client_id=&name=&platform=` | SSE. 붙는 순간 upsert. `hello` → `pending` 신호. Bearer 인증(fetch 스트리밍) |
| `POST /devices` `{client_id, name, platform}` | upsert — 폰 앱이 로그인 직후 부른다 |
| `GET /devices[?include_hidden=true]` | 내 기기 + `online`. 즐겨찾기 → 켜짐 → 나머지 |
| `PATCH /devices/{id}` `{name?, is_favorite?, is_hidden?}` | 부분 수정 |
| `DELETE /devices/{id}` | 기기 삭제(밀린 전송 포함) |
| `POST /transfers` multipart `file, to_device_id, from_device_id?` | 보내기 |
| `GET /transfers?device_id=` | 그 기기로 온, 안 받은 전송 |
| `GET /transfers/{id}/content` | 내려받기(게이트웨이 스트리밍) |
| `DELETE /transfers/{id}` | 받음 확인 = 삭제. 보낸 쪽 취소도 같은 경로 |

남의 기기·전송은 403 이 아니라 **404** 다(채팅 세션과 같은 이유 — 존재 자체를 흘리지 않는다).

## PC 수신 화면 (`/beemate`) — 2단계

- 설정 없이 바로 받는다. 저장 위치 기본값은 브라우저 다운로드 폴더다. 전용 폴더(File System
  Access API, Chrome·Edge)와 알림은 원할 때만 켠다 — 둘 다 사용자 클릭 없이는 브라우저가
  허용하지 않아 미리 설정해 줄 수 없다.
- 같은 화면에 **기기 관리**: 이름(눌러서 바로 바꾸기), ★ 즐겨찾기, "보낼 곳에 표시", 삭제.
- 이미지: PNG 로 바꿔 클립보드에 쓴다(Chrome 클립보드는 `image/png` 만 받는다) + 폴더에 사본.
  탭이 뒤에 있으면 브라우저가 쓰기를 막는다 → 알림을 띄우고, 누르면 탭이 앞으로 오며 복사한다.
- 그 외: 폴더에 저장. 폴더 핸들은 IndexedDB 에 두고, 브라우저 재시작 뒤 권한 확인이 한 번 뜰 수 있다.

## 폰 앱 — 3단계

Flutter. mini-drive 로그인(`/api/auth/login`·`/refresh` 가 토큰을 본문으로 준다) →
`flutter_secure_storage`. refresh 는 7일 회전이라 7일 넘게 안 쓰면 다시 로그인한다.
