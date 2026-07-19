# Phase 8 — 공유 드라이브 UX: 실시간 반영(SSE)·즐겨찾기·최근 항목

작성: 2026-07-19. 사용자 결정: LLM 기능(Phase 7) 전면 제거 후 파일 공유 코어에 집중.
이 문서는 Phase 8 세 기능의 상세 설계다.

## 8-1 파일 변경 실시간 반영 (SSE)

현재 파일 목록은 액션 후 재조회 방식이라 다른 사용자의 변경이 보이지 않는다.
SSE 로 변경 이벤트를 밀어 같은 폴더를 보는 사용자에게 실시간 반영한다.

**백엔드**
- `GET /api/files/events` — SSE 스트림. 인증은 기존 챗 SSE 와 같은 패턴
  (fetch 스트리밍 + Authorization 헤더; EventSource 는 헤더 불가라 쓰지 않는다).
- 전송 계층: Redis pub/sub 채널 `file-events` — 프로세스/워커 수와 무관하게 동작.
- 발행 지점(파일 서비스 뮤테이션): 업로드 완료·새 버전·폴더 생성·이름 변경·이동·
  소프트 삭제·복원·권한 부여/회수. 페이로드:
  `{type, file_id, parent_folder_id, actor_id, name, ts}`.
- **구독자별 권한 필터**: 이벤트의 파일에 구독자가 read 이상일 때만 전달
  (`get_access_level` — 기존 사용자 단위 Redis 캐시 재사용). 소유자는 항상 통과.
  이 필터가 없으면 타인 파일의 존재/이름이 메타데이터로 유출된다.
- keepalive ping 30초. 구독 해제는 연결 종료로.

**프론트**
- 파일 브라우저가 스트림을 구독하고, 이벤트의 `parent_folder_id` 가 현재 보고 있는
  폴더면 목록을 재조회한다(300ms 디바운스로 연속 이벤트 병합). 다른 폴더 이벤트는 무시.
- 내가 방금 수행한 액션의 에코 이벤트(actor_id == 나)는 이미 화면에 반영돼 있으므로
  재조회 생략 가능(단순화를 위해 v1 은 함께 재조회해도 무방).
- 연결 끊김 시 지수 백오프 재연결(1s→2s→…max 30s), 재연결 성공 시 1회 전체 재조회.

## 8-2 즐겨찾기

**스키마** (마이그레이션 0009)
```
file_favorites (
  user_id BIGINT FK users ON DELETE CASCADE,
  file_id BIGINT FK files ON DELETE CASCADE,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, file_id)
)
```

**API**
- `PUT /api/files/{id}/favorite` / `DELETE /api/files/{id}/favorite` — 토글.
  read 이상 접근 가능한 파일만 등록 가능(403), 멱등.
- `GET /api/files/favorites?page&size` — 내 즐겨찾기 목록(파일 메타 포함).
  삭제된 파일·접근 권한을 잃은 파일은 숨긴다(행은 유지 — 권한 복구 시 다시 보임).
- 파일 목록/단건 응답에 `is_favorite: bool` 파생 필드(EXISTS 서브쿼리).

**UI**
- 행 hover 시 별 아이콘 토글(리스트·그리드 공통), 즐겨찾기 상태면 항상 표시.
- 사이드바에 "즐겨찾기" 항목 → 가상 폴더 화면(기존 파일 목록 컴포넌트 재사용,
  미리보기/다운로드/이동 등 기존 행 액션 동일 동작).

## 8-3 최근 이용 콘텐츠

**스키마** (0009 동일 리비전)
```
file_recents (
  user_id BIGINT FK users ON DELETE CASCADE,
  file_id BIGINT FK files ON DELETE CASCADE,
  last_accessed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, file_id)
)
```
- 기록 시점: **미리보기·다운로드** 성공 시 upsert(폴더 열람은 소음이라 제외).
- 보존: 사용자당 최신 100개 유지(upsert 시 초과분 삭제 — 단순 서브쿼리).

**API**
- `GET /api/files/recent?limit=20` — 최근 항목(최신순, 삭제·접근불가 제외).

**UI**
- 드라이브 홈(루트) 상단에 "최근 항목" 카드 스트립(최대 6개, 클릭 = 미리보기).
- 사이드바 "최근" 항목 → 전체 최근 목록 화면.

## 구현 순서
1. **8-a 백엔드**: 마이그레이션 0009 → SSE(발행/구독/권한 필터) → 즐겨찾기·최근 API
   → 유닛/통합 테스트.
2. **8-b 프론트**: SSE 구독·자동 갱신 → 별 토글·즐겨찾기 뷰 → 최근 스트립·뷰.
3. 완료 후 사용자 컨펌.

## 제외 (백로그)
- 오프라인/충돌 병합, 웹소켓 양방향, 폴더 열람 기반 최근 기록, 즐겨찾기 폴더 정렬.
