"""파일/폴더 API 요청·응답 스키마 (PRD 6.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class FileResponse(BaseModel):
    """파일 또는 폴더 메타데이터 (오브젝트 스토리지 키는 노출하지 않는다)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    parent_folder_id: int | None
    name: str
    mime_type: str | None = None
    size: int
    is_folder: bool
    is_deleted: bool
    current_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # 파생 필드 — 자동 영구 삭제 예정 시각(휴지통 목록에서만 채운다).
    # 보존 기간이 0(자동 정리 비활성)이면 None. spec/trash-retention-purge.md
    purge_at: datetime | None = None
    # 파생 필드 (Phase 8-2) — 서비스가 File 인스턴스에 부착하면 채워지고, 없으면 False.
    is_favorite: bool = False
    # 파생 필드 — 조상 폴더 경로("내 드라이브 / A / B"). 최근·즐겨찾기 목록에서 위치 표기용.
    # 서비스가 부착하지 않으면 빈 문자열(일반 목록 응답은 breadcrumb 로 위치를 알 수 있어 생략).
    location: str = ""
    # 파생 필드 (통합 드라이브 — Unix 유사 소유자/그룹/권한 컬럼). 서비스가 부착하지 않으면 기본값.
    #  - owner_name:  파일 소유자(files.user_id)의 표시명.
    #  - group_names: 소유 항목이면 내가 공유한 그룹명들, 공유받은 항목이면 접근을 부여한 그룹명들.
    #  - permission:  요청자의 유효 권한. 소유자는 "owner", 그 외에는 read/write/manage.
    owner_name: str = ""
    group_names: list[str] = Field(default_factory=list)
    permission: str = "owner"
    # 파생 필드 (spec/wiki-index.md) — 위키 인덱싱 상태. 목록에서 배지로 쓴다.
    # off | pending | indexing | ready | stale | failed. **null 을 쓰지 않는다.**
    wiki_status: str = "off"
    # 이 항목 **자신의** 위키 명시값(files.wiki_enabled 3상태) — None=상속, True=명시 ON,
    # False=명시 OFF. 목록에서 "이 폴더는 전사 위키" 표시를 다는 데 쓴다: 폴더는 인덱싱 상태가
    # 없어서(wiki_status 가 늘 off) 이 값이 아니면 목록에서 위키 폴더를 알아볼 방법이 없다.
    # 들어가 보기 전에, 그리고 파일을 끌어다 놓기 전에 알아야 한다(spec 「폴더 상속 사고 경로」).
    #
    # 이름을 나눈 이유: 목록 최상위의 `wiki_enabled` 는 **지금 보고 있는 폴더의 유효값**(상속
    # 포함)이고 이건 **항목 자신의 명시값**이다. 같은 이름이면 두 값을 섞어 읽게 된다.
    wiki_declared: bool | None = Field(
        default=None, validation_alias=AliasChoices("wiki_declared", "wiki_enabled")
    )


class CrumbResponse(BaseModel):
    """breadcrumb 한 칸 — 눌러서 열 수 있는 폴더."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class BreadcrumbResponse(BaseModel):
    """URL 로 연 폴더의 경로 (GET /api/files/{id}/breadcrumb).

    crumbs 는 루트 **바로 아래**부터 그 폴더 자신까지이며, 요청자가 실제로 열 수 있는
    칸만 들어 있다. 루트 칸은 넣지 않는다 — 화면이 shared 에 따라 "내 드라이브" 또는
    "내 드라이브 / 공유"를 앞에 붙인다.
    """

    crumbs: list[CrumbResponse]
    # 그룹 공유로 닿은 경로인가. True 면 crumbs 위쪽은 남의 드라이브라 잘려 있다.
    shared: bool


class FileListResponse(BaseModel):
    """페이지네이션된 파일 목록 (PRD 6.2 GET /api/files)."""

    items: list[FileResponse]
    total: int
    page: int
    size: int
    # 파생 필드 (spec/wiki-index.md) — **지금 보고 있는 폴더**의 유효 위키 여부.
    # 업로드 경고에 쓴다. 위키 켜진 폴더에 올리면 그 순간 전사 공개되므로 올리기 전에 알아야 한다.
    #
    # 항목이 아니라 목록에 붙는 이유: 경고를 봐야 하는 사람이 `write` 권한자인데, 그에게는
    # 위키 상태 API(`/files/{id}/wiki`)가 404 다(발행 권한자만 본다). 폴더를 열 수 있으면
    # 그 폴더가 무엇을 하는 곳인지도 알아야 한다.
    #
    # 항목별 표시는 `items[].wiki_declared` 가 맡는다(이 값은 상속을 포함한 **유효**값이라
    # 항목마다 다시 계산하지 않는다).
    wiki_enabled: bool = False


class FolderCreateRequest(BaseModel):
    """폴더 생성 (PRD 6.2 POST /api/files). parent_id 생략 시 루트 폴더 하위."""

    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None


class BatchUploadItem(BaseModel):
    """배치 업로드 항목별 결과. 성공이면 file, 실패면 code/detail 이 채워진다."""

    path: str
    status: Literal["created", "error"]
    file: FileResponse | None = None
    code: int | None = None
    detail: str | None = None


class BatchUploadResponse(BaseModel):
    """배치 업로드 결과 (POST /api/files/batch).

    부분 실패가 정상 경로이므로 항목이 전부 실패해도 HTTP 200 이다. HTTP 상태는 "배치 요청이
    처리됐는가"를 뜻하고 개별 성패는 items 가 말한다.

    folders 는 개수가 아니라 **경로 → 폴더 id 맵**이며, 이번에 만든 것과 기존 것을 가리지 않고
    이 요청이 해석한 전 경로를 담는다. 클라이언트가 배치에 담지 않은 큰 파일을 단일/재개
    업로드 경로로 보낼 때 parent_id 를 여기서 꺼낸다.
    """

    items: list[BatchUploadItem]
    folders: dict[str, int]
    succeeded: int
    failed: int


class FileRenameRequest(BaseModel):
    """이름 변경 (PRD 6.2 PUT /api/files/{id})."""

    name: str = Field(min_length=1, max_length=255)


class FileMoveRequest(BaseModel):
    """다른 폴더로 이동 (PRD 6.2 POST /api/files/{id}/move). parent_id 생략/null 이면 루트."""

    parent_id: int | None = None


class FileVersionResponse(BaseModel):
    """버전 히스토리 항목 (PRD 3.3, 6.2). 오브젝트 키는 노출하지 않는다."""

    version: int
    size: int
    mime_type: str | None = None
    uploaded_by: int
    uploaded_by_name: str
    uploaded_at: datetime
    is_current: bool


class FileVersionListResponse(BaseModel):
    """파일 버전 목록 (PRD 6.2 GET /api/files/{id}/versions)."""

    file_id: int
    current_version: int
    items: list[FileVersionResponse]


class ArchiveTicketRequest(BaseModel):
    """폴더/다중 선택 ZIP 다운로드 티켓 발급 (POST /api/files/download-archive-ticket).

    file_ids 는 폴더와 파일을 섞어 담을 수 있다. 폴더는 하위 전체가 상대 경로 그대로 담긴다.
    """

    file_ids: list[int] = Field(min_length=1)


class DownloadTicketResponse(BaseModel):
    """단기 일회성 다운로드 티켓 (브라우저 대용량 다운로드용).

    `url` 로 무헤더 GET 하면 스트리밍 다운로드가 시작된다. 티켓은 60초 후 만료되며 1회만 유효.
    """

    ticket: str
    url: str
    expires_in: int


class PreviewStreamResponse(BaseModel):
    """영상 미리보기 응답 — 바이트 대신 재생할 주소를 준다 (PRD 3.2).

    프론트는 `url` 을 `<video src>` 에 그대로 걸고, 브라우저가 Range 로 필요한 구간만 받아 간다.
    티켓은 마지막 사용 시점부터 `expires_in` 초 뒤 만료된다(재생 중에는 계속 갱신).
    """

    kind: str = "video"
    mime: str
    url: str
    expires_in: int
