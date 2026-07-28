"""위키 인덱싱 API 요청·응답 스키마 (spec/wiki-index.md)."""

from __future__ import annotations

from pydantic import BaseModel


class WikiSetRequest(BaseModel):
    """위키 설정 변경 (PUT /api/files/{id}/wiki).

    두 축은 독립이고 각각 생략 가능하다(부분 갱신) —
      enabled : 인덱싱 명시값. null 을 명시하면 상속으로 되돌린다.
      public  : `@전사` read 부여/회수. 생략하면 건드리지 않는다.

    `enabled` 는 null 이 '상속'이라는 의미를 갖기 때문에 '생략'과 구분해야 한다.
    Pydantic 의 `model_fields_set` 으로 라우터에서 구분한다.
    """

    enabled: bool | None = None
    public: bool | None = None


class WikiFolderScopeResponse(BaseModel):
    """폴더 토글이 실제로 덮는 범위 — 확인 문구용.

    "md·html 12개가 인덱싱됩니다 (PDF 8개, pptx 3개는 제외)" 를 그리려면 제외 사유별
    개수가 필요하다. 켰을 때 무엇이 공개되는지 소유자가 알고 켜게 하는 게 목적이다.
    """

    target_count: int
    skipped_by_format: int
    skipped_by_size: int
    skipped_by_permission: int


class WikiStateResponse(BaseModel):
    """파일/폴더의 위키 상태 (GET·PUT 공통 응답)."""

    file_id: int
    is_folder: bool
    # 유효 인덱싱 여부 — 자기 명시값이 없으면 조상에서 상속된 값.
    enabled: bool
    # 이 파일 자신에 명시값이 있는가. false 면 상속 중이라 UI 가 '상속됨'으로 표기한다.
    explicit: bool
    # 그 값이 어디서 왔는지(상속 출처). 자기 자신이면 file_id 와 같다.
    source_file_id: int | None
    # `@전사` read 직접 부여 여부 (전사 공개 체크박스 상태).
    public: bool
    # 이 파일을 인덱싱할 수 있는가 + 안 되는 이유. 토글 비활성화와 사유 표기에 쓴다.
    indexable: bool
    reason: str | None = None
    # 인덱싱 진행 상태 — pending | indexing | ready | failed | stale. 트리가 없으면 null.
    status: str | None = None
    indexed_version: int | None = None
    # 폴더일 때만 채운다.
    folder_scope: WikiFolderScopeResponse | None = None


class WikiDocumentItem(BaseModel):
    """위키에 인덱싱된 문서 한 건 (목록용)."""

    file_id: int
    name: str
    owner_display_name: str
    status: str
    version: int
    indexed_at: str | None
    node_count: int | None


class WikiDocumentListResponse(BaseModel):
    items: list[WikiDocumentItem]
    total: int


class WikiAskRequest(BaseModel):
    """위키 질의 (POST /api/wiki/ask)."""

    question: str


class WikiCitation(BaseModel):
    """답변 근거 — 클릭하면 그 파일의 해당 줄로 이동한다.

    앵커가 페이지가 아니라 **줄 번호**인 것은 md 트리의 좌표가 line_num 이기 때문이다
    (page_index 는 PDF 경로 전용이고 v1 은 md/html 만 다룬다).
    """

    file_id: int
    file_name: str
    node_id: str
    node_title: str
    line_num: int


class WikiAskResponse(BaseModel):
    answer: str
    citations: list[WikiCitation]
    searched_documents: int
