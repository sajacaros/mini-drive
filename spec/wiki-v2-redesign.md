# 위키 v2 재설계 — "위키 공유" 체크 기반 전사 단일 위키

작성: 2026-07-19. Phase 7-3(스페이스 기반 위키) 검토 후 사용자 결정으로 재설계.

## 1. 배경과 결정

Phase 7-3 위키는 스페이스(personal/group 스코프) 단위로 청중을 고정하고, "권한 경계 =
컴파일 경계" 불변식(그룹이 read 가능한 원본만 소스 등록 허용)으로 유출을 막았다. 사용자
검토 결과 다음 문제가 확인됐다:

- 위키를 공유하려면 그룹 스페이스를 별도로 만들어야 한다 — 원하는 것은 "원본 접근 제어와
  별개로, 공유하기로 한 문서의 위키를 모두가 보는 것".
- 스페이스 루트 폴더가 생성자의 내 드라이브에 생겨 드라이브를 오염시킨다.
- 청중을 원본 권한에서 자동 파생하는 대안(페이지별 ACL, 개인별 index)은 다중 소스 페이지의
  청중 산정·집계 파일(index/log)의 뷰어별 필터링·출처 추적·LLM 교차 오염 문제로 기각.

**확정 모델 (사용자 결정 2026-07-19):**

- 드라이브 파일/폴더에 **"위키에 공유" 체크**를 둔다. 체크 = 소유자의 명시적 **출판** 행위.
- 체크된 콘텐츠는 **전사 단일 위키**로 컴파일되고, **모든 로그인 사용자가 같은 위키를 본다**.
- 드라이브의 개인/그룹 공유 권한과 **완전 분리** — 원본은 기존 권한 그대로 보호되고
  (위키 페이지의 출처 링크는 클릭 시점 권한 재검증 → 비권한자는 403), 공개되는 것은
  LLM 이 컴파일한 위키 페이지뿐이다.
- 스페이스 개념(wiki_spaces, personal/group 스코프)은 **제거**한다.

기존 불변식 "위키 독자 ⊆ 원본 독자"는 "**소유자 동의(체크) = 전사 출판**"으로 대체된다.
UI 는 체크 시 "원본 권한과 무관하게 요약이 모든 사용자에게 공개됩니다"를 명시 경고한다.

## 2. 세부 결정 사항

| # | 항목 | 결정 | 근거 |
|---|------|------|------|
| D1 | 체크 권한자 | **항목 소유자만** (admin 도 불가) | 출판은 소유자 동의. admin 내용 접근 불가 원칙 유지 |
| D2 | 폴더 체크의 컴파일 범위 | 서브트리 중 **체크한 사람 소유 파일만**, 항상 재귀 (`recursive` 옵션 제거) | 그룹 write 권한자가 남의 폴더에 만든 파일이 섞일 수 있음 — 타인 파일을 대신 출판하지 않는다 |
| D3 | 위키 페이지 저장 위치 | **시스템 사용자 소유** 폴더 (`시스템 root/Wiki`) | 개인 드라이브 오염 제거. 시스템 사용자는 로그인 불가 |
| D4 | 전 사용자 읽기 구현 | **권한 서비스 특례**: 위키 루트 하위 파일은 모든 로그인 사용자 READ | "전체 사용자" 시스템 그룹 방식은 그룹 관리 UI 오염·가입 훅·멤버십 드리프트 부담이 커서 기각. 특례는 `get_access_level` 한 곳에 국한 |
| D5 | 체크 해제 시 | 페이지 자동 삭제 없음, log.md 기록, Lint 가 정리 안내 | 기존 소스 제거 정책 유지 (컴파일된 지식은 자산) |
| D6 | Lint 실행 권한 | 로그인 사용자 누구나 | 결정적 자동 수정(index 정합화)뿐이라 위험 낮음. 전사 자산의 공동 관리 |
| D7 | 챗 답변 승격 | 로그인 사용자 누구나, 승격 모달에 전사 공개 경고 | 승격도 출판 동의 행위. 인용 링크는 권한 재검증되므로 원본 유출 없음 |
| D8 | 기존 스페이스 2개 | 마이그레이션에서 폐기(테이블 drop). 잔존 위키 폴더(bob 드라이브의 "솔루션본부 위키" id=210, "tesy" id=270)는 정책대로 드라이브에 남김 → 수동 삭제 안내 | 실데이터는 실패한 ingest 잡뿐 |
| D9 | 컴파일 규칙·Lint 규칙 | 변경 없음 (2단계 LLM 호출, 규칙 6항, locked 보호, append-only log) | 청중 모델만 바뀌고 컴파일 파이프라인은 동일 |

## 3. 데이터 모델 변경 (alembic 0007)

```
wiki_spaces              → DROP TABLE
chat_sessions.space_id   → DROP COLUMN (FK 먼저), wiki_scope BOOLEAN NOT NULL DEFAULT false 추가
wiki_sources             → 재구성: PK (file_id)
                            file_id BIGINT PK FK files(id) ON DELETE CASCADE
                            status VARCHAR(20)          -- queued/indexed/stale/failed (기존과 동일)
                            last_ingested_version INT NULL
                            added_by BIGINT FK users(id)  -- = 파일 소유자 (D1)
                            created_at TIMESTAMPTZ
                            (space_id, recursive 제거)
wiki_jobs                → space_id 제거 (나머지 동일: file_id, kind, status, retries, error)
app_settings             → key 'wiki_root_folder_id', 'wiki_system_user_id' (부트스트랩 시 기록)
files                    → 스키마 변경 없음. "체크 상태" = wiki_sources 행 존재 (진실 소스 단일화)
```

기존 wiki_sources/wiki_jobs 행은 폐기한다(실패 잡 이력뿐). 다운그레이드는 역방향 재생성.

**위키 부트스트랩 (lazy, 멱등):** 첫 공유 체크(또는 첫 위키 조회) 시 —
시스템 사용자 생성(`wiki-system@internal.invalid`, is_active=false, 로그인 불가, 대용량
quota) → 그 root 하위 `Wiki` 폴더 생성 → `index.md`/`log.md` 스텁 업로드 →
app_settings 기록. `ON CONFLICT DO NOTHING` 패턴으로 동시 부트스트랩 레이스 방어
(setup 서비스와 동일 패턴).

## 4. 백엔드 변경

### 4.1 권한 서비스 (D4)

`permissions_service.get_access_level()` 에 특례 추가: 대상 파일의 조상 체인에 위키 루트
폴더가 있으면(자기 자신 포함) 모든 로그인 사용자에게 READ. 구현은 기존 조상 recursive CTE
와 같은 패턴의 EXISTS 쿼리 + `wiki_root_folder_id` 는 app_settings 값을 프로세스 캐시.
기존 사용자 단위 권한 캐시(perm:{ugen}:...)가 그대로 결과를 감싼다. `group_access_level`
은 불변(그룹 단위 판정에 위키 특례 없음). 쓰기 권한은 특례 없음 — 위키 페이지 쓰기는
서비스 내부에서 시스템 사용자 자격으로만 수행한다.

효과: 파일 목록/미리보기/다운로드/버전/RAG 검색(2단계 필터)이 **코드 변경 없이** 위키
페이지에 대해 전 사용자 동작한다.

### 4.2 위키 API (routes/wiki.py 전면 개정)

```
GET    /api/wiki                  위키 개요 — root_folder_id, index_entries, recent_log,
                                  sources[{file_id, file_name, status, last_ingested_version, added_by}]
                                  (로그인 사용자 누구나)
POST   /api/wiki/sources          {file_id} — 위키 공유 체크. 소유자만(403). 부트스트랩 →
                                  wiki_sources upsert(queued) → wiki_jobs(ingest) → arq 큐잉
DELETE /api/wiki/sources/{file_id} 체크 해제. 소유자만. log.md 기록 (D5)
POST   /api/wiki/lint             Lint 실행 (D6)
GET    /api/wiki/jobs             잡 이력 (로그인 사용자 누구나)
POST   /api/wiki/promote          {message_id, title} — 챗 답변 승격 (D7, 종전 스페이스 승격 대체)
```

제거: `/api/wiki/spaces*` 전부. 스키마(schemas/wiki.py)도 대응 개정.

### 4.3 파일 API

- 목록/단건 응답(FileNode)에 `wiki_shared: bool` 추가 — wiki_sources LEFT JOIN 파생 필드.
  (프론트 배지·토글 표시용. 성능: 목록 쿼리에 EXISTS 서브쿼리 1개)

### 4.4 위키 서비스 (services/wiki.py)

- 스페이스 인가 함수(`can_access_space`/`ensure_access`/`ensure_write`/`scope_can_read`) 제거.
- `register_source(actor, file_id)`: `file.user_id == actor.id` 검증(D1)만. 폴더/파일 무관.
- `ingest_source(file_id)`: 대상 산출 = 파일이면 [file], 폴더면
  `eligible_descendant_files(file_id)` 중 **added_by 소유 파일만** (D2). 이후 파일당
  `compile_source()` 동일. 페이지 owner = 시스템 사용자.
- `wiki_file_scope()` (구 space_scope 대체): 위키 루트 서브트리 ∪ 등록 소스별
  (소유 파일 한정) 서브트리 — 챗 위키 범위의 검색 후보 집합.
- `run_lint`: space 파라미터 제거 외 동일. stale/권한 리포트 중 "스코프 read 불가" 항목은
  "소스가 소유자 변경/삭제됨" 계열로 문구만 조정.

### 4.5 챗 (routes/chat.py, services/chat*)

- 세션 생성/수정: `space_id` → `wiki_scope: bool`. true 면 검색 후보를
  `wiki_file_scope()` 로 교집합 + 위키 페이지 우선 배치(기존 wiki_page_ids 로직 유지).
- 원본 소스 파일은 여전히 사용자별 2단계 권한 필터를 통과해야 검색된다 — 즉 위키 범위
  챗에서도 **원본 스니펫은 권한자에게만, 위키 페이지는 모두에게** 노출된다(일관성 유지).
- 승격 API 는 4.2 의 `/api/wiki/promote` 로 이동.

## 5. 프론트 변경

- **드라이브 (FileBrowserPage)**: 행 컨텍스트 메뉴에 "위키에 공유"/"위키 공유 해제" 토글
  (소유자 항목에만 노출, `wiki_shared` 배지 표시). 체크 확인 모달: "원본 권한과 무관하게
  LLM 요약이 **모든 사용자에게 공개**됩니다. 폴더는 내 소유 파일만 포함됩니다." 경고.
- **위키 (/wiki)**: 목록 페이지 삭제, 단일 위키 화면으로 통합(구 상세 페이지 재사용) —
  카탈로그(index_entries → 페이지 미리보기), 공유 소스 현황(상태 배지), 최근 컴파일 로그,
  잡 이력(진행 중 폴링), Lint 실행. "드라이브에서 보기"는 위키 루트 폴더(공유 탐색 라우트).
  소스 추가 픽커는 유지하되 내 소유 항목만 선택 가능.
- **챗 (ChatPage)**: 스페이스 선택 셀렉트 → "위키 범위" 토글 하나. 승격 모달에 전사 공개
  경고 추가.
- 라우팅: `/wiki/:id` 제거. api/wiki.ts 전면 개정.

## 6. PRD 반영

- 3.7.1 불변식 개정: "권한 경계 = 컴파일 경계" → "출판 동의 경계 = 컴파일 경계"
  (소유자 체크 = 전사 출판, 원본 보호는 출처 링크 권한 재검증으로 유지).
- 5.14~5.16 스키마, 6.8~6.9 API 명세를 본 문서 기준으로 갱신.
- 마일스톤에 Phase 7-4(위키 v2 재설계) 추가.

## 7. 구현 순서 (Phase 7-4)

1. **7-4a 백엔드**: 마이그레이션 0007 → 부트스트랩/권한 특례 → 위키 서비스·API 개정 →
   챗 wiki_scope → 유닛/통합 테스트 개정.
2. **7-4b 프론트**: 드라이브 토글·배지 → 위키 단일 화면 → 챗 토글 → api 클라이언트.
3. 각 단계 완료 시 커밋(기능별 커밋 워크플로우), 7-4 완료 후 사용자 컨펌.

## 8. 테스트 계획

**유닛/통합 (integration_wiki.py 개정):**
- 비소유자 체크 403, 소유자 체크 201 → wiki_sources/잡 생성.
- 폴더 체크 시 타인 소유 파일 제외 (bob 폴더에 carol 이 만든 파일 → 컴파일 제외).
- 권한 특례: carol(비권한자)이 위키 페이지 preview 200 / **원본 파일은 403 유지**.
- admin: 위키 열람 가능(로그인 사용자), 원본 접근 불가 유지.
- 체크 해제 → log.md 기록, 페이지 잔존, Lint 리포트.
- 챗 wiki_scope: 위키 페이지는 모두 검색, 원본 스니펫은 권한자만.
- 부트스트랩 멱등성(동시 체크 레이스).

**실전 E2E (사용자 지정 시나리오):**
- bob 으로 **Research 폴더(file_id=9)만** 위키 공유 체크.
  - 규모: 하위 문서 157개·폴더 59개 → LLM 호출 약 157×2회. 필요 시 하위 폴더 하나로
    먼저 스모크 후 전체 진행 권장.
- carol/admin 으로 위키 카탈로그·페이지 열람 확인, 원본 403 확인.
- 챗 위키 범위 질의 — bob 은 원본 인용 포함, carol 은 위키 페이지 인용만.
- **전제 조건: Upstage 크레딧 충전** (현재 키 정지 상태 — 컴파일·임베딩 모두 Upstage.
  인덱싱도 98/196 에서 중단돼 있어 충전 후 미인덱스 파일 재큐잉 필요, 메모리의 재개 절차 참조).

## 9. 백로그 (이번 범위 제외)

- 팀 단위 위키(비전사 청중): 필요해지면 "스페이스 = 루트 폴더 권한 청중" 모델을 재도입.
- admin 의 소스 정리 권한(퇴사자 소유 공유 해제 등).
- wiki_links 그래프(P2, 기존 백로그 유지).
