/**
 * 백엔드 API 응답/요청 타입 (backend/app/schemas 와 1:1 대응).
 * 진실 소스는 backend 스키마이며, 여기서는 프론트 소비에 필요한 형태만 미러링한다.
 */

// 가입 코드제 전환(Phase 6) 이후 사용자 상태는 active/inactive 뿐 (승인제 pending/rejected 제거).
export type UserStatus = "active" | "inactive";
// super_admin: 셋업 최초 계정. 다른 사용자에게 admin 권한을 부여/회수할 수 있는 최상위 역할.
export type UserRole = "user" | "admin" | "super_admin";

/** GET /api/auth/me, 그리고 login/register 이후 사용자 정보. */
export interface User {
  id: number;
  email: string;
  display_name: string;
  avatar_url: string | null;
  role: UserRole;
  status: UserStatus;
  storage_used: number;
  max_storage: number;
  created_at: string;
  updated_at: string;
}

/** POST /api/auth/login, /api/auth/refresh. */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

/** POST /api/auth/register. */
export interface RegisterResponse {
  id: number;
  email: string;
  status: UserStatus;
  message: string;
}

/** 파일 또는 폴더 메타데이터 (GET /api/files, /api/files/{id}). */
export interface FileNode {
  id: number;
  user_id: number;
  parent_folder_id: number | null;
  name: string;
  mime_type: string | null;
  size: number;
  is_folder: boolean;
  is_deleted: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  /**
   * 자동 영구 삭제 예정 시각. 휴지통 목록에만 채워지는 파생 필드이며, 서버의 보존 기간이
   * 0(자동 정리 비활성)이면 null 이다 (spec/trash-retention-purge.md).
   * 보존 기간 자체는 따로 내려오지 않는다 — 필요하면 purge_at - deleted_at 으로 역산한다.
   */
  purge_at?: string | null;
  /** 내 즐겨찾기 여부 (Phase 8-2). 목록/단건 응답의 파생 필드, 없으면 false. */
  is_favorite: boolean;
  /** 조상 폴더 경로("내 드라이브 / A / B"). 최근·즐겨찾기 응답에만 채워지는 파생 필드. */
  location?: string;
  /** 소유자 표시 이름 (Unix 스타일 소유자 열). 목록/단건 응답의 파생 필드. */
  owner_name?: string;
  /** 공유 그룹명 목록. 소유자 시점: 이 항목이 공유된 그룹들, 수신자 시점: 접근을 부여한 그룹. */
  group_names?: string[];
  /** 요청 사용자의 유효 권한. owner=소유자, 그 외 그룹 권한 수준. */
  permission?: "owner" | "read" | "write" | "manage";
}

/** POST /api/files/batch 의 항목별 결과. 성공이면 file, 실패면 code/detail 이 채워진다. */
export interface BatchUploadItem {
  path: string;
  status: "created" | "error";
  file: FileNode | null;
  code: number | null;
  detail: string | null;
}

/**
 * POST /api/files/batch (배치 업로드).
 *
 * 부분 실패가 정상 경로라 항목이 전부 실패해도 HTTP 200 이다.
 * `folders` 는 개수가 아니라 경로 → 폴더 id 맵으로, 배치에 담지 않은 큰 파일을
 * 단일/재개 업로드로 보낼 때 parent_id 를 여기서 꺼낸다.
 */
export interface BatchUploadResponse {
  items: BatchUploadItem[];
  folders: Record<string, number>;
  succeeded: number;
  failed: number;
}

/** GET /api/files (페이지네이션). */
export interface FileListResponse {
  items: FileNode[];
  total: number;
  page: number;
  size: number;
}

/** 파일 버전 히스토리 항목 (GET /api/files/{id}/versions). */
export interface FileVersion {
  version: number;
  size: number;
  mime_type: string | null;
  uploaded_by: number;
  uploaded_by_name: string;
  uploaded_at: string;
  is_current: boolean;
}

/** 버전 목록 응답 (내림차순). */
export interface FileVersionListResponse {
  file_id: number;
  current_version: number;
  items: FileVersion[];
}

/** 단기 1회용 다운로드 티켓 (POST .../download-ticket). url 을 무헤더 GET 하면 스트리밍. */
export interface DownloadTicketResponse {
  ticket: string;
  url: string;
  expires_in: number;
}

/**
 * 재개 가능 업로드 세션 상태 (PRD 3.2). 개시(POST) 응답이자 재개(GET) 응답.
 * 클라이언트는 part_size 로 청크를 나누고(마지막 파트 제외 정확히 part_size), uploaded_parts 로
 * 빠진 파트만 이어올린다.
 */
export interface ResumableSession {
  session_id: string;
  kind: "new" | "version";
  file_id: number;
  part_size: number;
  total_parts: number;
  total_size: number;
  uploaded_parts: number[];
  received_bytes: number;
  expires_at: string;
}

/** 단일 파트 업로드 결과 (PUT .../parts/{n}). */
export interface ResumablePart {
  part_number: number;
  size: number;
  etag: string;
}

export type SharePermission = "read" | "download";

/** 소유자용 공유 링크 응답 (POST/GET /api/shares). */
export interface Share {
  id: number;
  file_id: number;
  file_name: string;
  /** 폴더 공유 여부 — 방문자는 하위 전체를 ZIP 으로 받는다. */
  is_folder: boolean;
  share_url: string;
  permission: string;
  is_active: boolean;
  password_required: boolean;
  expires_at: string | null;
  max_downloads: number | null;
  download_count: number;
  created_at: string;
  /**
   * 접근 통계 (PRD 3.4). view_count/last_access_at 는 Redis 근사 집계라 재시작/flush 시
   * 소실될 수 있다. download_count 는 DB 정확값(횟수 제한과 직결).
   */
  view_count: number;
  last_access_at: string | null;
}

/** 페이지네이션된 내 공유 링크 목록 (GET /api/shares). total 은 active 필터 적용 후 개수. */
export interface ShareListResponse {
  items: Share[];
  total: number;
  page: number;
  size: number;
}

/** 단건 공유 통계 (GET /api/shares/{id}/stats). */
export interface ShareStats {
  share_id: number;
  view_count: number;
  last_access_at: string | null;
  download_count: number;
}

export interface ShareCreateRequest {
  file_id: number;
  permission?: SharePermission;
  expires_at?: string | null;
  password?: string | null;
  max_downloads?: number | null;
}

/** 공개(무인증) 공유 메타 (GET /api/public/shares/{shareUrl}). */
/** 폴더 공유 탐색 경로의 한 칸 (루트→현재). */
export interface SharePublicCrumb {
  id: number;
  name: string;
}

/** 폴더 공유 목록의 한 항목 (방문자에게 보여줄 최소 정보). */
export interface SharePublicEntry {
  id: number;
  name: string;
  is_folder: boolean;
  size: number;
  mime_type: string | null;
  updated_at: string;
}

/** 폴더 공유 웹 탐색 응답 (POST /api/public/shares/{shareUrl}/list). */
export interface ShareFolderListing {
  folder: SharePublicCrumb;
  breadcrumbs: SharePublicCrumb[];
  entries: SharePublicEntry[];
}

export interface SharePublicMeta {
  file_name: string;
  /** 폴더 공유면 웹 탐색(list)이 가능하고, 전체/하위 폴더는 ZIP 으로 내려온다. */
  is_folder: boolean;
  size: number;
  mime_type: string | null;
  permission: string;
  password_required: boolean;
  expires_at: string | null;
}

/** admin 사용자 목록 항목 (GET /api/admin/users). */
export interface AdminUser {
  id: number;
  email: string;
  display_name: string;
  avatar_url: string | null;
  role: UserRole;
  status: UserStatus;
  storage_used: number;
  max_storage: number;
  created_at: string;
  updated_at: string;
}

export interface AdminUserListResponse {
  items: AdminUser[];
  total: number;
  page: number;
  size: number;
}

// --- admin 대시보드/통제 (PRD 6.7) -----------------------------------------

/** 공유 링크 활성/비활성 집계. */
export interface ShareCounts {
  active: number;
  inactive: number;
  total: number;
}

/** 사용량 상위 사용자 (GET /api/admin/stats). */
export interface TopUser {
  email: string;
  storage_used: number;
  max_storage: number;
}

/** 인스턴스 대시보드 통계 (GET /api/admin/stats). 메타데이터 집계만 (3.6.4). */
export interface AdminStats {
  total_users: number;
  users_by_status: Record<string, number>;
  total_files: number;
  total_folders: number;
  total_storage_used: number;
  total_shares: ShareCounts;
  total_groups: number;
  top_users: TopUser[];
}

/** 전체 그룹 목록 항목 (GET /api/admin/groups). */
export interface AdminGroup {
  id: number;
  name: string;
  description: string | null;
  owner_id: number;
  owner_email: string;
  is_active: boolean;
  member_count: number;
  file_count: number;
  created_at: string;
}

export interface AdminGroupListResponse {
  items: AdminGroup[];
  total: number;
  page: number;
  size: number;
}

/** 전체 공유 링크 목록 항목 (GET /api/admin/shares). 파일 내용은 노출하지 않음 (3.6.4). */
export interface AdminShare {
  id: number;
  file_id: number;
  file_name: string;
  created_by: number;
  creator_email: string;
  permission: string;
  is_active: boolean;
  download_count: number;
  max_downloads: number | null;
  expires_at: string | null;
  created_at: string;
}

export interface AdminShareListResponse {
  items: AdminShare[];
  total: number;
  page: number;
  size: number;
}

/** 감사 로그 항목 (GET /api/admin/audit-logs). */
export interface AdminAuditLog {
  id: number;
  actor_id: number;
  actor_email: string;
  action: string;
  target_type: string;
  target_id: number | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface AdminAuditLogListResponse {
  items: AdminAuditLog[];
  total: number;
  page: number;
  size: number;
}

/** 이메일 정확 일치 사용자 조회 (GET /api/users/lookup). */
export interface UserLookup {
  id: number;
  email: string;
  display_name: string;
}

// --- 그룹 (PRD 6.4) --------------------------------------------------------

export type GroupRole = "owner" | "admin" | "member";

/** 그룹 기본 정보. */
export interface Group {
  id: number;
  name: string;
  description: string | null;
  owner_user_id: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** 그룹 목록 항목 — 멤버 수 + 내 역할 포함 (GET /api/groups). */
export interface GroupSummary extends Group {
  member_count: number;
  my_role: GroupRole | null;
  /** 소유자(owner_user_id)의 표시명. */
  owner_display_name: string;
}

export interface GroupListResponse {
  items: GroupSummary[];
  total: number;
  page: number;
  size: number;
}

/** 활성 그룹원. */
export interface GroupMember {
  user_id: number;
  email: string;
  display_name: string;
  role: GroupRole;
  joined_at: string;
}

/** 그룹 상세 — 기본 정보 + 멤버 목록 (GET /api/groups/{id}). */
export interface GroupDetail extends GroupSummary {
  members: GroupMember[];
}

// --- 권한 (PRD 6.5/6.6) ----------------------------------------------------

export type GroupPermissionLevel = "read" | "write" | "manage";

/** 파일에 직접 부여된 그룹 권한. */
export interface DirectPermission {
  group_id: number;
  group_name: string;
  permission: string;
  inherit_to_children: boolean;
  granted_at: string;
  expires_at: string | null;
  granted_by: number;
}

/** 조상 폴더에서 상속되어 유효한 그룹 권한. */
export interface InheritedPermission {
  group_id: number;
  group_name: string;
  permission: string;
  source_file_id: number;
  source_file_name: string;
  depth: number;
  expires_at: string | null;
}

/** 파일 권한 목록 — 직접 부여 + 유효 상속 (GET /api/files/{id}/permissions). */
export interface FilePermissions {
  file_id: number;
  direct: DirectPermission[];
  inherited: InheritedPermission[];
}

/** 내 유효 권한 (GET /api/permissions/check/{fileId}). */
export interface PermissionCheck {
  file_id: number;
  permission: "read" | "write" | "manage" | "none";
  via: "owner" | "group" | "admin" | "none";
  source_file_id: number | null;
}

/** 공유된 항목(부여 지점) 하나 (GET /api/files/shared-with-me). */
export interface SharedItem {
  file: FileNode;
  group_id: number;
  group_name: string;
  permission: string;
}

export interface SharedWithMeResponse {
  items: SharedItem[];
}

// --- 셋업 위저드 (PRD 3.6.2, 6.1) ------------------------------------------

/** GET /api/setup/status (무인증). admin 0명이면 setup_required=true. */
export interface SetupStatusResponse {
  setup_required: boolean;
  admin_exists: boolean;
}

/** POST /api/setup 요청 — 첫 admin + 초기 가입 코드 + 기본 할당량. */
export interface SetupRequest {
  admin_email: string;
  admin_password: string;
  /** 관리자 표시 이름. 비우면 서버가 "Administrator" 로 대체. */
  admin_display_name?: string;
  /** 미지정(null/생략) 시 서버가 자동 생성. */
  signup_code?: string | null;
  default_max_storage?: number;
}

/** POST /api/setup 응답 — 발급된 가입 코드 포함. */
export interface SetupResponse {
  admin_email: string;
  signup_code: string;
  default_max_storage: number;
  message: string;
}

// --- 가입 코드 관리 (PRD 6.7) ----------------------------------------------

/** 가입 코드 (GET/POST/PATCH /api/admin/signup-codes). */
export interface SignupCode {
  id: number;
  code: string;
  memo: string;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  is_active: boolean;
  created_by: number;
  created_at: string;
}

export interface SignupCodeListResponse {
  items: SignupCode[];
  total: number;
  page: number;
  size: number;
}

/** 발급 요청 — code 미지정 시 서버 자동 생성. */
export interface SignupCodeCreateRequest {
  memo?: string;
  expires_at?: string | null;
  max_uses?: number | null;
  code?: string | null;
}

/**
 * 수정 요청 (부분 갱신). 백엔드는 exclude_unset 이므로 JSON 에 포함한 키만 반영된다.
 * expires_at/max_uses 에 명시적 null 을 보내면 무기한/무제한으로 초기화된다.
 */
export interface SignupCodeUpdateRequest {
  is_active?: boolean;
  memo?: string;
  expires_at?: string | null;
  max_uses?: number | null;
}

// --- 데일리 투두 + 반복 루틴 -------------------------------------------------

/** pending=빈칸(미완료), done=완료(v), failed=수행 실패(x — 명시적 미달성). */
export type TodoStatus = "pending" | "done" | "failed";
export type RoutineFrequency = "daily" | "weekly" | "monthly";

export interface Routine {
  id: number;
  title: string;
  frequency: RoutineFrequency;
  /** 월=0 .. 일=6. weekly 일 때만 채워짐. */
  days_of_week: number[];
  /** 1~31. monthly 일 때만 채워짐. 그 달에 없는 날짜면 그 달은 건너뛴다. */
  day_of_month: number | null;
  is_active: boolean;
  sort_order: number;
  created_at: string;
}

export interface RoutineListResponse {
  items: Routine[];
}

export interface RoutineCreateRequest {
  title: string;
  frequency: RoutineFrequency;
  days_of_week: number[];
  day_of_month?: number | null;
}

export interface RoutineUpdateRequest {
  title?: string;
  frequency?: RoutineFrequency;
  days_of_week?: number[];
  day_of_month?: number | null;
  is_active?: boolean;
  sort_order?: number;
}

export interface TodoItem {
  id: number;
  date: string;
  title: string;
  status: TodoStatus;
  routine_id: number | null;
  routine_title: string | null;
  sort_order: number;
  completed_at: string | null;
  created_at: string;
}

export interface TodoDayResponse {
  date: string;
  items: TodoItem[];
  total: number;
  done: number;
  /** 수행 실패(x). 달성률 분모에 포함된다. */
  failed: number;
  pending: number;
}

export interface DailyPoint {
  date: string;
  total: number;
  done: number;
  failed: number;
  pending: number;
}

export interface RoutineStat {
  routine_id: number | null;
  title: string;
  done: number;
  actionable: number;
  rate: number;
}

export interface TodoReport {
  range_start: string;
  range_end: string;
  total: number;
  done: number;
  failed: number;
  pending: number;
  /** done / total — 실패(x)와 빈칸 모두 분모에 들어간다. */
  completion_rate: number;
  current_streak: number;
  longest_streak: number;
  daily: DailyPoint[];
  routines: RoutineStat[];
}

// --- 위키 (spec/wiki-index.md) ----------------------------------------------

/**
 * 인덱싱 상태. **null 이 없는 총체 함수**라 UI 가 분기를 빠뜨리지 않는다.
 *
 * `stale` 은 새 버전이 올라왔지만 재인덱싱이 아직 안 끝난 상태다. 이때도 구 트리로 계속
 * 답하므로 검색이 멈추지 않는다.
 */
export type WikiStatus =
  | "off"
  | "pending"
  | "indexing"
  | "ready"
  | "stale"
  | "failed";

/** 폴더 토글이 실제로 덮는 범위 — 확인 문구에 쓴다. */
export interface WikiFolderScope {
  target_count: number;
  skipped_by_format: number;
  skipped_by_size: number;
  skipped_by_permission: number;
}

export interface WikiState {
  file_id: number;
  is_folder: boolean;
  /** 유효 인덱싱 여부 — 자기 명시값이 없으면 조상에서 상속된 값. */
  enabled: boolean;
  /** 이 항목 자신에 명시값이 있는가. false 면 상속 중이다. */
  explicit: boolean;
  /** 그 값이 어디서 왔는지. 자기 자신이면 file_id 와 같다. */
  source_file_id: number | null;
  /** `@전사` read 직접 부여 여부 (전사 공개 체크박스 상태). */
  public: boolean;
  /** 인덱싱 대상인가 + 아니면 이유. 토글 비활성화와 사유 표기에 쓴다. */
  indexable: boolean;
  reason: string | null;
  status: WikiStatus;
  indexed_version: number | null;
  /** 폴더일 때만 채워진다. */
  folder_scope: WikiFolderScope | null;
}

export interface WikiDocumentItem {
  file_id: number;
  name: string;
  owner_display_name: string;
  status: WikiStatus;
  version: number;
  indexed_at: string | null;
  node_count: number | null;
}

export interface WikiDocumentList {
  items: WikiDocumentItem[];
  total: number;
}

export interface WikiCitation {
  file_id: number;
  file_name: string;
  node_id: string;
  node_title: string;
  /** 근거 앵커는 페이지가 아니라 **줄 번호**다 (md 트리의 좌표가 line_num). */
  line_num: number;
}

export interface WikiAnswer {
  answer: string;
  citations: WikiCitation[];
  searched_documents: number;
}
