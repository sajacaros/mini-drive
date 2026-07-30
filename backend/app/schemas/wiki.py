"""위키 인덱싱 API 요청·응답 스키마 (spec/wiki-index.md)."""

from __future__ import annotations

from pydantic import BaseModel


class WikiSetRequest(BaseModel):
    """위키 설정 변경 (PUT /api/files/{id}/wiki).

    축은 하나다 — `enabled` 를 켜면 인덱싱과 전사 공개가 함께 걸린다(spec 「왜 스위치가
    하나인가」). 예전에 있던 `public` 필드는 없앴다.

    `enabled` 는 null 이 '상속으로 되돌리기'라는 의미를 갖기 때문에 '생략'과 구분해야 한다.
    Pydantic 의 `model_fields_set` 으로 라우터에서 구분한다.
    """

    enabled: bool | None = None


class WikiFolderScopeResponse(BaseModel):
    """폴더 토글이 실제로 덮는 범위 — 확인 문구용.

    "md·html 12개가 인덱싱됩니다 (PDF 8개, pptx 3개는 제외)" 를 그리려면 제외 사유별
    개수가 필요하다. 켰을 때 무엇이 공개되는지 소유자가 알고 켜게 하는 게 목적이다.
    """

    target_count: int
    skipped_by_format: int
    skipped_by_size: int
    skipped_by_permission: int
    # 소유자가 직접 끈 파일 — 폴더를 켜도 되살아나지 않는다.
    skipped_by_optout: int = 0


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
    # 이 파일을 인덱싱할 수 있는가 + 안 되는 이유. 토글 비활성화와 사유 표기에 쓴다.
    indexable: bool
    reason: str | None = None
    # 인덱싱 진행 상태 — off | pending | indexing | ready | stale | failed.
    # **null 을 쓰지 않는다** — UI 가 분기를 빠뜨리지 않도록 항상 값이 있다.
    status: str
    indexed_version: int | None = None
    # 폴더일 때만 채운다.
    folder_scope: WikiFolderScopeResponse | None = None


class WikiDocumentItem(BaseModel):
    """위키에 인덱싱된 문서 한 건 (목록용)."""

    file_id: int
    name: str
    owner_display_name: str
    # 소유자 드라이브 안에서의 폴더 경로("문서함 / 규정"). 최상위 파일이면 빈 문자열.
    # 보는 사람과 무관하게 같은 값이다 — 전사 위키 목록은 사람마다 다르지 않다.
    location: str = ""
    status: str
    version: int
    indexed_at: str | None
    node_count: int | None


class WikiDocumentListResponse(BaseModel):
    items: list[WikiDocumentItem]
    total: int


class WikiCatalogNode(BaseModel):
    """카탈로그의 절 하나. 자식 절을 그대로 품는 재귀 구조다.

    `line_num` 은 원문의 1-based 줄 번호로, 미리보기 앵커가 그대로 쓴다.
    `summary` 는 인덱싱이 붙인 절 요약이고, 짧은 절은 요약 대신 본문이 그대로 들어 있다.
    """

    node_id: str
    title: str
    line_num: int
    summary: str | None = None
    nodes: list[WikiCatalogNode] = []


class WikiCatalogResponse(BaseModel):
    """문서 한 건의 카탈로그 (GET /api/wiki/documents/{file_id}).

    질의가 절을 고를 때 보는 것이 이 트리다 — 화면이 검색과 같은 것을 보여준다.
    """

    file_id: int
    name: str
    owner_display_name: str
    location: str = ""
    status: str
    version: int
    indexed_at: str | None
    node_count: int | None
    nodes: list[WikiCatalogNode]


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
    # 질의 대상 문서 전체 수. 0 이면 "찾을 곳이 없다" — "답이 없다"와 다른 말이라 화면이 구분한다.
    searched_documents: int
    # 이번 질문에서 실제로 트리를 들여다본 문서 수. 문서가 많으면 질문과 관련된 노드부터
    # 예산만큼만 프롬프트에 올리므로(spec 「후보 선별」) 이 값이 searched_documents 보다 작다.
    # 좁혀졌다는 사실을 화면에 드러내지 않으면 사용자가 "전부 뒤졌는데 없다"로 읽는다.
    examined_documents: int = 0
