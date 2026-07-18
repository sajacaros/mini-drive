# Mini Drive – 사내 파일 공유/관리 서비스 PRD

**작성일:** 2026-07-17  
**저자:** (미정)  
**검토자:** (미정)  
**상태:** Phase 1~5 구현 완료, Phase 6(셋업 위저드 + 가입 코드제) 구현 중

---

## 목차

1. [개요](#1-개요)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [기능 요구사항](#3-기능-요구사항)
4. [기술 스택](#4-기술-스택)
5. [데이터베이스 설계](#5-데이터베이스-설계)
6. [API 설계](#6-api-설계)
7. [Docker Compose 구성](#7-docker-compose-구성)
8. [nginx 게이트웨이 설정](#8-nginx-게이트웨이-설정)
9. [개발 마일스톤](#9-개발-마일스톤)
10. [보안 설계](#10-보안-설계)
11. [모니터링 & 로깅](#11-모니터링--로깅)
12. [리스크 & 대응](#12-리스크--대응)
13. [고찰: 기존 설계와의 연계](#13-고찰기존-설계와의-연계)
14. [비고](#14-비고)

---

## 1. 개요

### 1.1 목적

사내 구성원 대상 **경량 구글 드라이브** — 파일 업로드·다운로드·버전 관리·공유 URL 생성·권한 관리까지 웹 기반으로 제공하며, 운영 전체는 Docker 기반으로 재현 가능한 환경을 지향한다.

### 1.2 타겟 사용자

- 사내 임직원(모든 부서)
- **시스템 관리자(Admin)**: 인스턴스 운영자. 가입 코드 관리(발급/만료/비활성화), 사용자 계정 관리(활성/비활성, 할당량), 전체 공유 링크 통제, 스토리지 모니터링 (→ [3.6 시스템 관리자](#36-시스템-관리자-admin))
- **그룹 관리자**: 소속 그룹의 멤버·파일/폴더 권한 관리 (그룹 역할 `owner`/`admin`, 시스템 관리자와 별개 축)

### 1.3 그룹 기반 접근 제어 (Group-based Access Control)

GitHub의 Team 구조와 유사하게 **그룹(Group) → 그룹원(Member) → 리소스 권한** 계층으로 접근 권한을 통제한다. Mini Drive 인스턴스 자체가 단일 조직 역할을 하므로, 별도의 조직(Organization) 테이블은 두지 않고 인스턴스 내 그룹으로 관리한다.

| 계층 | 설명 |
|---|---|
| **그룹(Group)** | 부서·프로젝트·팀 단위로 생성 (예: "개발1팀", "디자인", "2025-ERP프로젝트") |
| **그룹원(Member)** | 사용자를 그룹에 소속. 역할(`owner`, `admin`, `member`) 부여 |
| **리소스 권한** | 파일/폴더 단위로 그룹에 읽기·쓰기·관리 권한 부여. 하위 폴더로 상속 가능 |

### 1.4 비기능 요구사항

| 항목 | 기준 |
|---|---|
| 가용성 | 99.9% (단일 노드 기준) |
| 파일 크기 제한 | 10 GB |
| 응답 시간 | 업로드/다운로드 API p95 ≤ 3초 (100 MB 이하) |
| 확장성 | 수평 확장 가능한 S3 호환 스토리지 기반 |
| 일관성 | 파일 메타데이터 강한 일관성(관계형 DB) |
| 운영 | Docker Compose 단일 명령으로 전체 스택 기동 |
| **그룹 권한 일관성** | 권한 변경 즉시 반영, 상속 관계 실시간 갱신, 권한 캐시 무효화 |

---

## 2. 시스템 아키텍처

### 2.1 전체 데이터 흐름

```
React(FE)
  │  (1) 업로드: multipart → FastAPI
  │  (2) 다운로드: FastAPI 인가 → nginx → MinIO (X-Accel-Redirect)
  │  (3) 공유 URL: FastAPI 인가 → nginx (X-Accel-Redirect)
  │  (4) 버전 조회/복구: FastAPI → PostgreSQL
  ▼
FastAPI (Backend)
  ├─ Auth: JWT 기반 인증/인가
  ├─ GroupService: 그룹 CRUD, 멤버 관리, 권한 상속
  ├─ PermissionService: 리소스별 그룹 권한 검사·상속·재정의
  ├─ StorageService: MinIO S3 추상화 계층
  ├─ FileService: 메타데이터 CRUD
  ├─ VersionService: 버전 히스토리 관리
  └─ AdminService: 사용자/공유 링크 운영 관리, 감사 로그
  ▼
MinIO (Object Storage)
  └─ Bucket: minidrive
       ├── users/{userId}/{fileId}     ← 현재 버전 원본
       ├── versions/{fileId}/v{n}      ← 버전 스냅샷
       └── thumbnails/{fileId}.png     ← 썸네일
  ▼
PostgreSQL (Metadata DB)
  ├─ users / groups / group_members 테이블
  ├─ files / file_versions 테이블
  ├─ shares 테이블 (공유 링크 정보)
  ├─ file_group_permissions 테이블
  └─ audit_logs 테이블 (관리자 행위 감사)
  ▼
Redis (Cache / Token Store)
  ├─ refresh 토큰 저장·회전 (로그아웃 시 폐기)
  ├─ 권한 판정 결과 캐시 (변경 시 무효화)
  └─ rate limiting 카운터
  ▼
nginx (Reverse Proxy / Gateway)
  ├─ 정적 파일 서빙 (React SPA)
  ├─ API 프록시 (FastAPI)
  └─ 내부 스트리밍 프록시 (X-Accel-Redirect → MinIO)
```

### 2.2 핵심 설계 결정

| 결정 | 설명 |
|---|---|
| **S3 추상화 계층** | `StorageService` 인터페이스(`put/get/presignGet/delete`)로 MinIO 접근을 감싸, 추후 AWS S3 전환 시 코드 변경 최소화 |
| **게이트웨이 다운로드 모델** | 브라우저에 presigned URL 직접 발급 ❌ → FastAPI 매 요청 인가 후 `X-Accel-Redirect`로 nginx→MinIO 스트리밍 ✅. 공유 링크 비활성화 시 **즉시 차단** 보장 |
| **앱 레벨 버전 관리** | MinIO 네이티브 버저닝 ❌, PostgreSQL `file_versions` 테이블이 진실 소스 ✅ |
| **Internal presign (60s TTL)** | nginx↔MinIO 간 presign은 브라우저 비노출, 60초 TTL, nginx만 접근 가능. MinIO 버킷은 익명 접근 완전 차단(`mc anonymous set none`) |
| **권한 상속 = 조회 시 판정** | 상속 권한을 행으로 물질화하지 않음. 명시적 부여만 저장하고, 조회 시 조상 경로를 recursive CTE로 판정 + Redis 캐시. 권한 변경 시 쓰기 폭증 없이 "즉시 반영" 요구 충족 |
| **시스템 admin / 그룹 admin 분리** | `users.role`(전역)과 `group_members.role`(그룹 내)은 별개 축. 시스템 admin은 운영 통제만, 파일 내용 접근 불가 |

---

## 3. 기능 요구사항

### 3.1 회원 관리

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **회원가입 (가입 코드제)** | 사내 이메일 + **관리자가 발급한 가입 코드** 입력으로 가입, 코드 검증 통과 시 **즉시 `active`** (승인 대기 없음). 코드는 만료일·최대 사용 횟수 제한 가능 | P0 |
| **로그인/로그아웃** | JWT 인증, 리프레시 토큰 갱신. 로그아웃 시 Redis에 저장된 refresh 토큰 폐기. 비활성 계정은 즉시 차단 — access 토큰 유효 기간 내 요청도 인가 시 `status = 'active'` 확인으로 차단 | P0 |
| **프로필 관리** | 아바타, 표시 이름, 비밀번호 변경 | P1 |
| **관리자 대시보드** | → [3.6 시스템 관리자](#36-시스템-관리자-admin) 참조 | P1 |

### 3.1.1 그룹 관리 (Group Management)

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **그룹 생성** | 조직 내 그룹 생성, 이름·설명 설정 | P0 |
| **그룹원 초대/제거** | 사용자를 그룹에 초대하거나 제거, 역할(`owner`/`admin`/`member`) 부여 | P0 |
| **그룹 목록 조회** | 조직 내 전체 그룹 목록 조회 | P0 |
| **그룹 상세 조회** | 그룹원 목록, 그룹 소유 파일/폴더 조회 | P1 |
| **그룹 수정/삭제** | 그룹 정보 수정, 그룹 삭제(소속 권한 일괄 제거) | P1 |
| **그룹 관리자 위임** | 그룹 소유자(`owner`)가 다른 멤버에게 관리자 권한 위임 | P2 |

### 3.1.2 그룹 권한 모델

| 권한 수준 | 설명 |
|---|---|
| **owner** | 그룹의 모든 권한 + 그룹 삭제/소유권 이전 가능 |
| **admin** | 그룹원 관리(추가/제거/역할 변경), 그룹 파일/폴더 권한 관리 |
| **member** | 그룹 권한으로 부여된 파일/폴더에 접근(읽기/쓰기/다운로드) |

### 3.1.3 파일/폴더 그룹 권한 상속

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **폴더 그룹 권한 부여** | 특정 폴더에 그룹에 대한 읽기·쓰기·관리 권한 부여 | P0 |
| **권한 상속** | 부모 폴더의 그룹 권한을 하위 폴더/파일에 자동 상속 | P0 |
| **권한 재정의** | 하위 폴더에서 상속된 권한을 개별적으로 재정의 가능 | P1 |
| **그룹 소유권 이전** | 그룹 소유 파일/폴더의 소유자가 퇴사 시 그룹 소유로 이전 | P1 |

### 3.1.4 UI 테마 선택 (Design Theme)

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **테마 선택** | 사용자는 다크/화이트 + 모던/게임보이 조합 중 선택 (총 4가지 테마) | P1 |
| **테마 저장** | 선택한 테마를 사용자 프로필에 저장, 로그인 시 자동 적용 | P1 |
| **기본 테마** | 시스템 설정(dark mode 선호도)에 따라 자동 선택, 수동 오버라이드 가능 | P2 |
| **테마 전환 애니메이션** | 테마 변경 시 부드러운 전환 애니메이션 (Tailwind transition 활용) | P2 |

#### 3.1.4.1 테마 매트릭스

|  | **모던 (Modern)** | **게임보이 (Game Boy)** |
|---|---|---|
| **다크 (Dark)** | 다크 모던 — 다크 배경 + 그라데이션 + 부드러운 그림자 + 세련된 아이콘 | 다크 게임보이 — 다크 배경 + 픽셀 폰트 + 8비트 아이콘 + 초록/검정 배색 |
| **라이트 (Light)** | 라이트 모던 — 흰/연회색 배경 + 미니멀 UI + 산세리프 + 부드러운 컬러 | 라이트 게임보이 — 크림 배경 + 픽셀 폰트 + 8비트 아이콘 + 연두/갈색 배색 |

#### 3.1.4.2 테마별 디자인 토큰

| 토큰 | 다크 모던 | 라이트 모던 | 다크 게임보이 | 라이트 게임보이 |
|---|---|---|---|---|
| `bg-primary` | `#0f172a` (slate-900) | `#f8fafc` (slate-50) | `#0a0a0a` (near-black) | `#f0ead6` (cream) |
| `bg-secondary` | `#1e293b` (slate-800) | `#e2e8f0` (slate-200) | `#1a1a1a` | `#d4c9a8` |
| `bg-accent` | `#3b82f6` (blue-500) | `#3b82f6` | `#00ff41` (green) | `#8bac0f` |
| `text-primary` | `#f1f5f9` (slate-100) | `#0f172a` (slate-900) | `#00ff41` | `#303030` |
| `text-secondary` | `#94a3b8` (slate-400) | `#64748b` (slate-500) | `#8bac0f` | `#5a5a3a` |
| `border-color` | `#334155` (slate-700) | `#e2e8f0` (slate-200) | `#333333` | `#c4b896` |
| `font-family` | `Inter, sans-serif` | `Inter, sans-serif` | `Press Start 2P, monospace` | `Press Start 2P, monospace` |
| `border-radius` | `8px~12px` | `8px~12px` | `0px~4px` (sharp) | `0px~4px` |
| `shadow` | `soft shadow` | `soft shadow` | `none` (flat) | `none` (flat) |
| `icon-style` | `Heroicons / Lucide` | `Heroicons / Lucide` | `8-bit pixel icons` | `8-bit pixel icons` |
| `transition` | `150ms ease` | `150ms ease` | `0ms` (instant) | `0ms` (instant) |

#### 3.1.4.3 테마 적용 방식 (Tailwind CSS)

- **CSS 변수 기반:** `:root`에 테마별 CSS 변수를 정의하고, Tailwind의 `@layer utilities`에서 참조
- **클래스 토글:** `<body>`에 `theme-modern-dark`, `theme-modern-light`, `theme-gameboy-dark`, `theme-gameboy-light` 클래스 적용
- **Tailwind Config 확장:** `tailwind.config.js`에서 `theme.extend`에 테마별 커스텀 색상·폰트 추가
- **React Context:** `ThemeContext`로 현재 테마를 전역 관리, `useTheme()` 훅으로 접근

```typescript
// 예시: ThemeContext
type ThemeMode = 'modern' | 'gameboy';
type ThemeScheme = 'dark' | 'light';
type Theme = `${ThemeScheme}-${ThemeMode}`; // 'dark-modern', 'light-gameboy' 등

interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  colors: Record<string, string>;
  fonts: { family: string; size: string };
}
```

---

### 3.2 파일 관리

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **파일 업로드** | 드래그 앤 드롭, multipart 스트리밍 업로드, 최대 10 GB | P0 |
| **재개 가능 업로드** | 1 GB 초과 파일은 청크 분할 + S3 Multipart Upload 기반 재개 가능 업로드 (중단 시 이어올리기) | P1 |
| **폴더 생성/구조** | `/users/{userId}/` 루트 기반 디렉터리 구조 | P0 |
| **파일 목록 조회** | 페이지네이션, 정렬, 필터링 | P0 |
| **파일 다운로드** | 게이트웨이 스트리밍 (인가+실시간 검증) | P0 |
| **파일 삭제** | 소프트 삭제(휴지통), 영구 삭제 | P1 |
| **파일 미리보기** | 텍스트/이미지/PDF 등 유형별 미리보기 | P2 |
| **썸네일 자동 생성** | 업로드 시 썸네일 생성 → `thumbnails/{fileId}.png` | P2 |

### 3.3 버전 관리

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **버전 히스토리 조회** | 파일별 버전 목록, 업로드 일시/크기 표시 | P0 |
| **이전 버전 다운로드** | 특정 버전 키로 게이트웨이 스트리밍 | P0 |
| **이전 버전 복구** | 과거 버전을 **새 버전으로 복사 생성** (이력 보존, 유실 0%) | P0 |
| **버전 충돌 감지** | `baseVersion` 불일치 시 409 Conflict 반환 | P1 |

### 3.4 공유 URL 관리

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **공유 링크 생성** | 읽기/다운로드 권한 부여, 만료일 설정, 비밀번호 옵션 | P0 |
| **공유 링크 비활성화** | 즉시 410 DISABLED 반환 (게이트웨이 모델) | P0 |
| **공유 링크 목록** | 내 파일 공유 현황 조회, 활성/비활성 상태 | P1 |
| **접근 통계** | 공유 링크 조회 수, 마지막 접근 시각 | P2 |
| **공유 링크 권한** | 읽기 전용 / 다운로드 전용 / 편집 권한 | P1 |

### 3.5 델타 동기화 (범위 제외)

블록 단위 저장·수정 블록만 동기화·롱폴링 알림은 **범위에서 제외**한다. 웹 브라우저 중심 서비스에서는 로컬 파일과의 지속 동기화 클라이언트가 없어 블록 단위 델타 동기화의 효용이 낮다. 대용량 업로드의 중단/재개 요구는 S3 Multipart 기반 재개 가능 업로드(3.2)가 담당한다.

### 3.6 시스템 관리자 (Admin)

#### 3.6.1 역할 모델

시스템 전역 역할은 `users.role` 컬럼(`user` | `admin`) 하나로 관리한다. **그룹 역할(`group_members.role`)과는 별개 축**이며, 팀 단위 관리는 그룹 역할이 담당하므로 중간 등급은 두지 않는다 (필요 시 추후 확장).

#### 3.6.2 첫 관리자 부트스트랩 — 셋업 위저드

"첫 가입자가 admin" 방식은 배포 직후 레이스 위험이, 환경변수 시드 방식은 기본 비밀번호 방치 위험이 있으므로 사용하지 않는다. 첫 admin은 **셋업 위저드**로 생성한다.

| 방식 | 설명 |
|---|---|
| **셋업 위저드 (기본)** | admin이 0명이면 프론트가 `/setup`으로 유도. 첫 admin 계정(이메일/비밀번호) + 초기 가입 코드 + 기본 할당량을 한 번에 설정하고 셋업을 영구 잠금. 백엔드는 admin 존재 시 셋업 API를 403으로 차단(동시 요청 레이스는 DB 제약/트랜잭션으로 방어) |
| **CLI 커맨드** | `python -m app.cli create-admin` — 운영 중 기존 사용자 승격·비상 복구용으로 유지 |

셋업에서 결정하는 값은 **애플리케이션 설정**(DB `app_settings`)뿐이다. 인프라 시크릿(PostgreSQL/MinIO/JWT)은 컨테이너 기동 전에 필요하므로 `.env`에 남는다.

#### 3.6.3 관리자 기능

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **가입 코드 관리** | 가입 코드 발급(메모·만료일·최대 사용 횟수)/목록·사용 현황 조회/비활성화. 모든 변경은 `audit_logs` 기록 | P0 |
| **사용자 관리** | 사용자 목록·사용량 조회, 활성/비활성 전환, 할당량(`max_storage`) 조정, role 변경 | P0 |
| **전체 그룹 조회** | 그룹 owner가 아니어도 전체 그룹/멤버 현황 조회 | P1 |
| **공유 링크 통제** | 전체 공유 링크 조회, 강제 비활성화 | P1 |
| **스토리지 통계** | 인스턴스 총 사용량, 사용자별 사용량, 파일 수 | P1 |
| **감사 로그 조회** | 관리자 행위 이력 조회 (→ 5.9 `audit_logs`) | P1 |

#### 3.6.4 접근 정책

- **admin은 사용자 파일의 메타데이터(파일명, 크기, 공유 현황)만 조회 가능하며, 파일 내용 다운로드는 불가**하다. 사내 서비스라도 프라이버시 기대를 보장한다.
- 퇴사자 파일 인수인계는 admin의 내용 접근이 아니라 **그룹 소유권 이전**(3.1.3)으로 해결한다.
- 모든 admin 행위(계정 비활성화, 할당량 변경, role 변경, 공유 링크 강제 차단)는 `audit_logs`에 기록한다.
- 인가는 FastAPI `require_admin` dependency로 `/api/admin/*` 라우터 전체에 일괄 적용한다.

---

## 4. 기술 스택

| 계층 | 기술 | 버전 |
|---|---|---|
| **Frontend** | React | 19.x |
| | TypeScript | 5.x |
| | Tailwind CSS | 4.x |
| | React Router | 7.x |
| | Zustand (상태 관리) | 5.x |
| | Axios | 1.x |
| **Backend** | Python | 3.12+ |
| | FastAPI | 0.115+ |
| | Pydantic | 2.x |
| | SQLAlchemy | 2.x (async) |
| | Alembic | 최신 |
| | python-multipart | 최신 |
| | MinIO SDK (S3 호환) | 최신 |
| | PyJWT | 최신 |
| **Object Storage** | MinIO | 최신 (Docker 이미지) |
| **Database** | PostgreSQL | 16+ |
| | pgvector (선택) | 최신 |
| **Cache / Token Store** | Redis | 7.x |
| **Reverse Proxy** | nginx | 1.27+ |
| **Auth** | JWT (access + refresh) | — |
| | argon2 (비밀번호 해싱) | 최신 |
| **Infrastructure** | Docker | 최신 |
| | Docker Compose | 최신 |

---

## 5. 데이터베이스 설계

### 5.1 users 테이블

```sql
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(100) NOT NULL DEFAULT '',
    avatar_url      VARCHAR(500),
    role            VARCHAR(20) NOT NULL DEFAULT 'user',  -- user / admin (시스템 전역 역할)
    status          VARCHAR(20) NOT NULL DEFAULT 'active',  -- active / inactive (가입 코드제 — 코드 검증 시 즉시 active)
    storage_used    BIGINT NOT NULL DEFAULT 0,
    max_storage     BIGINT NOT NULL DEFAULT 10737418240,  -- 10GB
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.2 files 테이블

```sql
CREATE TABLE files (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id),   -- 생성자(업로더)
    group_id        BIGINT REFERENCES groups(id),           -- 그룹 소유 파일 (NULL = 개인 소유)
    parent_folder_id BIGINT REFERENCES files(id),
    name            VARCHAR(255) NOT NULL,
    file_key        VARCHAR(500) NOT NULL,
    mime_type       VARCHAR(100),
    size            BIGINT NOT NULL,
    thumbnail_key   VARCHAR(500),
    is_folder       BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    base_version    INT NOT NULL DEFAULT 0,
    current_version INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- 같은 폴더 내 이름 중복 방지 (휴지통 파일은 제외)
CREATE UNIQUE INDEX uq_files_sibling_name
    ON files (parent_folder_id, name) WHERE is_deleted = FALSE;
CREATE INDEX idx_files_parent ON files (parent_folder_id);
CREATE INDEX idx_files_user   ON files (user_id);
```

> **루트 폴더**: 가입 시 사용자별 루트 폴더 행을 자동 생성하여 일반 파일/폴더의 `parent_folder_id`가 항상 NOT NULL이 되도록 한다 (루트 행만 `parent_folder_id IS NULL`). 이로써 위 unique 인덱스가 루트 레벨에서도 사용자 간 간섭 없이 동작한다.

### 5.3 file_versions 테이블

```sql
CREATE TABLE file_versions (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    object_key      VARCHAR(500) NOT NULL,
    size            BIGINT NOT NULL,
    mime_type       VARCHAR(100),
    uploaded_by     BIGINT NOT NULL REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (file_id, version)
);
```

### 5.4 shares 테이블

```sql
CREATE TABLE shares (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT NOT NULL REFERENCES files(id),
    created_by      BIGINT NOT NULL REFERENCES users(id),
    share_url       VARCHAR(64) UNIQUE NOT NULL,
    permission      VARCHAR(20) NOT NULL DEFAULT 'read',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    password_hash   VARCHAR(255),
    expires_at     TIMESTAMPTZ,
    max_downloads   INT,
    download_count  INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.5 groups 테이블

```sql
CREATE TABLE groups (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    description     TEXT,
    owner_user_id   BIGINT NOT NULL REFERENCES users(id),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name)
);
```

### 5.6 group_members 테이블

```sql
CREATE TABLE group_members (
    id              BIGSERIAL PRIMARY KEY,
    group_id        BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL DEFAULT 'member',  -- owner / admin / member
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at      TIMESTAMPTZ,  -- soft delete (NULL = 활성 멤버)
    UNIQUE (group_id, user_id)
);
```

> **재초대 처리**: soft delete(`removed_at`)와 `UNIQUE (group_id, user_id)`를 함께 쓰므로, 제거된 멤버 재초대는 새 행 INSERT가 아니라 기존 행 재활성화로 처리한다:
> `INSERT ... ON CONFLICT (group_id, user_id) DO UPDATE SET removed_at = NULL, role = EXCLUDED.role, joined_at = NOW()`

### 5.7 file_group_permissions 테이블

```sql
CREATE TABLE file_group_permissions (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    group_id        BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    permission      VARCHAR(20) NOT NULL DEFAULT 'read',  -- read / write / manage
    inherit_to_children BOOLEAN NOT NULL DEFAULT TRUE,    -- 하위 폴더/파일로 상속 여부
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,  -- 만료 시각 (NULL = 영구)
    granted_by      BIGINT NOT NULL REFERENCES users(id),
    UNIQUE (file_id, group_id)
);
```

> **상속 설계 결정 — 물질화하지 않고 조회 시 판정**
>
> 상속된 권한을 하위 파일마다 행으로 복사(물질화)하면 깊은 트리에서 폴더 권한 변경 한 번에 하위 전체 쓰기 폭증이 발생하여, "권한 변경 즉시 반영"(1.4) 요구와 충돌한다. 따라서:
> - 이 테이블에는 **명시적으로 부여한 권한만** 저장한다.
> - 권한 판정 시 대상 파일의 조상 경로를 recursive CTE로 따라가며 `inherit_to_children = TRUE`인 가장 가까운 권한을 적용한다.
> - **권한 재정의**(3.1.3)는 하위 폴더에 명시적 행을 추가하는 것으로 표현한다 (가까운 조상 우선이므로 자연스럽게 상위 권한을 덮어씀).
> - 판정 결과는 Redis에 캐시하고, 권한 변경·그룹 멤버 변경 시 관련 캐시를 무효화한다.

### 5.8 파일 소유자 변경 고려사항

`files.user_id`는 파일의 **생성자(업로더)**를 의미하며, **소유권(그룹 귀속 여부)**은 `files.group_id` 컬럼으로 구분한다. 그룹 소유 파일은 `group_id`가 지정되고, 개인 소유 파일은 `group_id IS NULL`이다. 권한 검사 시 그룹 소유권을 우선 확인한다.

### 5.9 audit_logs 테이블

관리자 행위(계정 비활성화, 할당량·role 변경, 공유 링크 강제 차단)와 권한 변경을 기록한다. "누가 이 링크를 껐나"에 답하기 위한 테이블이다.

```sql
CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    actor_id        BIGINT NOT NULL REFERENCES users(id),
    action          VARCHAR(50) NOT NULL,   -- 예: user.deactivate, user.quota_update, signup_code.create, signup_code.update, share.force_disable, permission.grant
    target_type     VARCHAR(30) NOT NULL,   -- user / group / file / share
    target_id       BIGINT,
    detail          JSONB,                  -- 변경 전/후 값 등
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_target ON audit_logs (target_type, target_id);
CREATE INDEX idx_audit_logs_actor  ON audit_logs (actor_id, created_at);
```

### 5.10 저장 용량 할당량의 원자적 갱신

`users.storage_used` 할당량 검사는 동시 업로드 시 레이스가 발생할 수 있으므로, 애플리케이션에서 읽고 비교하는 방식이 아니라 DB 레벨 원자적 갱신으로 강제한다:

```sql
UPDATE users
SET storage_used = storage_used + :size
WHERE id = :user_id AND storage_used + :size <= max_storage
RETURNING id;  -- 0 rows → 할당량 초과, 업로드 거부
```

### 5.11 signup_codes 테이블 (가입 코드)

```sql
CREATE TABLE signup_codes (
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(64) UNIQUE NOT NULL,      -- 추측 불가 랜덤 토큰
    memo            VARCHAR(200) NOT NULL DEFAULT '', -- 용도 메모 (예: "개발팀 온보딩")
    expires_at      TIMESTAMPTZ,                      -- NULL = 무기한
    max_uses        INTEGER,                          -- NULL = 무제한
    use_count       INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      BIGINT NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

가입 시 검증(활성 → 만료 → 사용 횟수)과 `use_count` 증가는 원자적으로 처리한다
(조건부 UPDATE ... RETURNING — 5.10과 동일 패턴으로 동시 가입 레이스 방어).

### 5.12 app_settings 테이블 (애플리케이션 설정)

```sql
CREATE TABLE app_settings (
    key         VARCHAR(64) PRIMARY KEY,   -- 예: 'setup_completed', 'default_max_storage'
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

셋업 위저드가 기록하는 애플리케이션 설정 저장소. `default_max_storage`는 신규 가입자의
`users.max_storage` 기본값으로 사용한다. 인프라 시크릿은 저장하지 않는다(3.6.2).

---

## 6. API 설계

### 6.1 인증

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/auth/register` | 회원가입 (body에 가입 코드 `signup_code` 필수 — 검증 통과 시 즉시 `active`) |
| `GET`  | `/api/setup/status` | 셋업 필요 여부 (admin 존재 여부, 무인증) |
| `POST` | `/api/setup` | 첫 부팅 셋업: 첫 admin + 초기 가입 코드 + 기본 할당량 (admin 존재 시 403) |
| `POST` | `/api/auth/login` | 로그인 (access + refresh JWT 반환) |
| `POST` | `/api/auth/refresh` | 리프레시 토큰으로 access 갱신 |
| `POST` | `/api/auth/logout` | 로그아웃 (Redis의 refresh 토큰 폐기) |
| `GET`  | `/api/auth/me` | 현재 사용자 정보 |

### 6.2 파일

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/files/upload` | 파일 업로드 (multipart) |
| `GET`  | `/api/files` | 파일/폴더 목록 (query: `parentId`, `page`, `size`) |
| `GET`  | `/api/files/{id}` | 파일 메타데이터 |
| `GET`  | `/api/files/{id}/download` | 게이트웨이 스트리밍 다운로드 |
| `GET`  | `/api/files/{id}/versions` | 버전 히스토리 |
| `GET`  | `/api/files/{id}/versions/{v}/download` | 특정 버전 다운로드 |
| `POST` | `/api/files/{id}/versions/{v}/restore` | 이전 버전 복구 |
| `POST` | `/api/files/{id}/delete` | 소프트 삭제 |
| `POST` | `/api/files/{id}/permanent-delete` | 영구 삭제 |
| `POST` | `/api/files` | 폴더 생성 |
| `PUT`  | `/api/files/{id}` | 이름 변경 등 메타데이터 업데이트 |

### 6.3 공유

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/shares` | 공유 링크 생성 |
| `GET`  | `/api/shares` | 내 공유 링크 목록 |
| `DELETE`| `/api/shares/{id}` | 공유 링크 비활성화 |
| `GET`  | `/api/public/shares/{shareUrl}` | 공유 메타 조회 (무인증. 없음 404 / 비활성·만료·삭제 410) |
| `POST` | `/api/public/shares/{shareUrl}/download` | 공유 다운로드 (무인증, body: password 옵션) |

> 공개 접근은 `/api/public/` prefix로 분리한다 — `/api/shares/{id}`(정수, 인증)와 `/{shareUrl}`(문자열, 무인증)의 라우팅 모호성을 제거하고 인증/무인증 경계를 명확히 하기 위함.

### 6.4 그룹 관리

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/groups` | 그룹 생성 |
| `GET`  | `/api/groups` | 조직 내 그룹 목록 조회 |
| `GET`  | `/api/groups/{id}` | 그룹 상세 조회 (그룹원, 권한 정보 포함) |
| `PUT`  | `/api/groups/{id}` | 그룹 정보 수정 |
| `DELETE`| `/api/groups/{id}` | 그룹 삭제 |
| `POST` | `/api/groups/{id}/members` | 그룹원 초대 (user_id, role 지정) |
| `DELETE`| `/api/groups/{id}/members/{userId}` | 그룹원 제거 |
| `PUT`  | `/api/groups/{id}/members/{userId}/role` | 그룹원 역할 변경 |
| `GET`  | `/api/groups/{id}/members` | 그룹원 목록 조회 |

### 6.5 파일 그룹 권한

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/files/{id}/permissions` | 파일/폴더에 그룹 권한 부여·upsert (group_id, permission, inherit_to_children, expires_at) |
| `GET`  | `/api/files/{id}/permissions` | 직접 부여 + 유효 상속 권한 목록 조회 (manage 권한 필요) |
| `PUT`  | `/api/files/{id}/permissions/{groupId}` | 권한 수정 (permission / inherit_to_children / expires_at) |
| `DELETE`| `/api/files/{id}/permissions/{groupId}` | 권한 제거 (캐시 무효화로 즉시 차단) |
| `GET`  | `/api/files/shared-with-me` | 내 그룹에 공유된 부여 지점 목록 (공유 탐색 진입점) |

> 폴더도 `files` 행이므로 별도 `/api/folders/*` 경로를 두지 않고 files로 통일한다. 상속 설정/해제는 PUT의 `inherit_to_children` 필드로 처리.

### 6.6 권한 검사 (내부용)

| Method | Endpoint | 설명 |
|---|---|---|
| `GET`  | `/api/permissions/check/{fileId}` | 현재 사용자가 해당 파일에 대해 가진 권한 조회 (read/write/manage) |
| `GET`  | `/api/permissions/inherited/{fileId}` | 파일의 상속된 권한 트리 조회 (디버깅/관리용) |

### 6.7 관리자 (Admin)

`require_admin` dependency로 라우터 전체에 인가를 일괄 적용한다. 모든 상태 변경 행위는 `audit_logs`에 기록된다.

| Method | Endpoint | 설명 |
|---|---|---|
| `GET`  | `/api/admin/users` | 사용자 목록 (query: `status` — `active`/`inactive`) |
| `POST` | `/api/admin/signup-codes` | 가입 코드 발급 (memo, expires_at, max_uses) |
| `GET`  | `/api/admin/signup-codes` | 가입 코드 목록·사용 현황 조회 |
| `PATCH`| `/api/admin/signup-codes/{id}` | 가입 코드 수정 (비활성화/재활성화, 만료·횟수 조정) |
| `PATCH`| `/api/admin/users/{id}` | 활성/비활성 전환, 할당량(`max_storage`) 조정, role 변경 |
| `GET`  | `/api/admin/groups` | 전체 그룹 목록 (멤버 수, 소유 파일 수 포함) |
| `GET`  | `/api/admin/shares` | 전체 공유 링크 목록 (query: `active`, `userId`) |
| `POST` | `/api/admin/shares/{id}/disable` | 공유 링크 강제 비활성화 |
| `GET`  | `/api/admin/stats` | 인스턴스 총 사용량, 사용자 수, 파일 수 |
| `GET`  | `/api/admin/audit-logs` | 감사 로그 조회 (query: `actorId`, `targetType`, `from`, `to`) |

> **주의**: admin API는 파일 **메타데이터만** 다룬다. 파일 내용 다운로드 엔드포인트는 admin 네임스페이스에 존재하지 않는다 (3.6.4 접근 정책).

---

## 7. Docker Compose 구성

```yaml
services:
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
    depends_on:
      - backend
    networks:
      - minidrive

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    # 호스트 포트 미노출 — 모든 트래픽은 nginx 게이트웨이 경유 (rate limiting/TLS 우회 방지)
    expose:
      - "8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:password@db:5432/minidrive
      - MINIO_ENDPOINT=minio:9000
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=change-me-in-production
      - JWT_SECRET=change-me-in-production
      - JWT_ALGORITHM=HS256
      - REDIS_URL=redis://redis:6379/0
      # 첫 admin 은 셋업 위저드로 생성한다 (3.6.2)
    depends_on:
      db:
        condition: service_healthy
      minio:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - minidrive

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    volumes:
      - ./frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    networks:
      - minidrive

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      - MINIO_ROOT_USER=minioadmin
      - MINIO_ROOT_PASSWORD=change-me-in-production
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - minidrive

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=minidrive
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - minidrive

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - minidrive

  mc:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      sleep 5;
      mc alias set local http://minio:9000 minioadmin change-me-...;
      mc mb --ignore-existing local/minidrive;
      mc anonymous set none local/minidrive;
      "
    # 주의: 익명 접근은 반드시 none — download로 열면 내부 네트워크에서 인가 없이
    # 객체를 읽을 수 있어 게이트웨이 모델(매 요청 인가)이 무력화됨.
    # nginx→MinIO 접근은 내부 전용 presigned URL(60초 TTL)로만 수행.
    networks:
      - minidrive

volumes:
  minio_data:
  pg_data:
  redis_data:

networks:
  minidrive:
    driver: bridge
```

---

## 8. nginx 게이트웨이 설정

### 8.1 다운로드/공유 스트리밍 프록시

```nginx
location /_minio/ {
    internal;
    proxy_pass http://minio:9000/;
    proxy_set_header Host minio:9000;
    proxy_set_header Authorization "";
    proxy_set_header X-Real-IP $remote_addr;

    # 대용량 다운로드: 디스크 버퍼링 없이 스트리밍
    proxy_buffering off;
}
```

> 공유 링크(`/api/shares/`)는 8.2의 `/api/` location이 포괄하므로 별도 location을 두지 않는다.

### 8.2 정적 서빙 (프론트엔드)

```nginx
server {
    listen 80;
    server_name _;

    # 최대 파일 크기 10 GB (1.4 비기능 요구사항)
    client_max_body_size 10g;

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # 대용량 업로드: nginx 디스크 버퍼링 없이 backend로 스트리밍
        proxy_request_buffering off;
        proxy_read_timeout  300s;
        proxy_send_timeout  300s;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    location /_minio/ {
        internal;
        proxy_pass http://minio:9000/;
        proxy_set_header Host minio:9000;
        proxy_set_header Authorization "";
    }
}
```

> **참고**: nginx 설정 변경 시 `docker compose up -d --force-recreate nginx`으로 컨테이너 재생성 필요 (단일 파일 bind-mount 시 inode 교체 문제 회피).

---

## 9. 개발 마일스톤

각 페이즈 완료 시 검토·컨펌 후 다음 페이즈로 진행한다. **현재 상태: Phase 1~5 완료(각 페이즈 게이트웨이 E2E 통과), Phase 6 구현 중(백엔드 진행 중, 프론트 예정).**

> Phase 1~5의 범위 기술은 당시 구현 기준의 기록이다. 가입 승인제와 admin 환경변수 시드는 Phase 6에서 가입 코드제·셋업 위저드로 대체된다(3.1, 3.6.2).

### Phase 1: MVP — 인증 + 파일 + 공유 링크 (2-3주) ✅

| 항목 | 내용 |
|---|---|
| **목표** | 인증 + 파일 업로드/다운로드 + 목록 조회 + 공유 링크 생성/비활성화 |
| **범위** | 프로젝트 스캐폴딩(backend/frontend/compose/nginx), Alembic 전체 스키마 마이그레이션, 인증(JWT + Redis refresh 회전, argon2, **가입 승인제**), 파일 CRUD(스트리밍 업로드, 게이트웨이 다운로드, 폴더, 소프트 삭제), 공유 링크(생성/비활성화/목록), 프론트 기본 UI(로그인, 파일 브라우저, 업로드, 공유) |
| **포함(admin)** | `users.role`/`status` 컬럼 + `require_admin` dependency + admin 부트스트랩(환경변수 시드) + **가입 승인/거절·사용자 활성/비활성 API와 최소 관리 화면** — 승인제이므로 admin 사용자 관리 없이는 서비스가 동작하지 않음 |
| **완료 조건** | 가입 신청→admin 승인→로그인→파일 업로드→다운로드→공유 링크 생성/비활성화 E2E 통과 |
| **제외** | 버전 관리, 그룹, 델타 동기화, 썸네일, 미리보기, admin 통계·감사 로그 대시보드(Phase 4) |

### Phase 2: 버전 관리 (1-2주) ✅

| 항목 | 내용 |
|---|---|
| **목표** | 파일 버전 히스토리 + 복구 + 특정 버전 다운로드 |
| **완료 조건** | 업로드 시 버전 자동 기록, 버전 목록 조회, 이전 버전 복구(새 버전 생성), 특정 버전 다운로드, `baseVersion` 충돌 감지(409) |

### Phase 3: 그룹 & 권한 (1-2주) ✅

| 항목 | 내용 |
|---|---|
| **목표** | 그룹 기반 접근 제어 (1.3절) 전체 구현 |
| **범위** | 그룹 CRUD·멤버 관리(재초대 upsert 포함), 파일/폴더 그룹 권한 부여, 조회 시 상속 판정(recursive CTE) + Redis 캐시·무효화, 권한 재정의, 그룹 소유권 이전, 그룹 관리 UI |
| **완료 조건** | 그룹 생성→멤버 초대→폴더 권한 부여→하위 파일 상속 접근→권한 회수 즉시 차단 E2E 통과 |

### Phase 4: 운영 안정성 + Admin (1-2주) ✅

| 항목 | 내용 |
|---|---|
| **목표** | 프로덕션 준비 + 운영 관리 도구 |
| **완료 조건** | Nginx 게이트웨이 정제, 건강 체크, 구조화 로깅, 메트릭(Prometheus), rate limiting, 백업 스크립트, Docker Compose 프로덕션 프로파일, **나머지 admin 기능(스토리지 통계, 그룹/공유 링크 통제, 감사 로그) + 대시보드 UI 완성** |

### Phase 5: 고도화 (선택, 2-3주) ✅

| 항목 | 내용 |
|---|---|
| **목표** | 재개 가능 업로드, 썸네일, 미리보기, 접근 통계, UI 테마(4종) — 델타 동기화는 범위 제외(3.5) |
| **완료 조건** | 청크 재개 업로드, 이미지/PDF/텍스트 미리보기, 공유 링크 통계, 테마 선택(3.1.4) |

### Phase 6: 셋업 위저드 + 가입 코드제 🚧 구현 중

| 항목 | 내용 |
|---|---|
| **목표** | 첫 부팅 셋업 위저드(3.6.2)로 admin 부트스트랩 대체, 가입 승인제 → 가입 코드제(3.1) 전환 |
| **범위** | `app_settings`(5.12)·`signup_codes`(5.11) 테이블, `/api/setup/*`, 가입 코드 검증 가입, admin 코드 관리 API/UI(6.7), 승인 API·UI 및 `pending`/`rejected` 상태·`ADMIN_*` 환경변수 시드 제거 |
| **완료 조건** | 빈 DB 부팅→셋업 위저드→admin 생성→코드 발급→코드 가입→즉시 로그인 E2E 통과, 셋업 재진입 차단 확인 |
| **상태** | 백엔드(마이그레이션·setup API·코드 가입·admin 코드 관리·승인제 제거) 구현 중, 프론트(셋업 위저드 페이지·가입 폼·코드 관리 UI) 예정 |

---

## 10. 보안 설계

| 항목 | 방안 |
|---|---|
| **인증** | JWT (access 15분 + refresh 7일), argon2 비밀번호 해싱 |
| **토큰 폐기** | refresh 토큰은 Redis에 저장·회전(rotation), 로그아웃/비활성화 시 즉시 폐기. 인가 시 매 요청 `status = 'active'` 확인으로 access 토큰 유효 기간 내 우회 차단 |
| **가입 코드제** | 가입에는 관리자 발급 코드가 필수(만료·사용 횟수 제한, 원자적 소모). 코드 없이는 계정 생성 불가 — 사내 시스템 무단 가입 차단 |
| **인가** | FastAPI Dependency Injection 기반 소유자/공유자/관리자/그룹 멤버 검증. admin API는 `require_admin` 일괄 적용 |
| **admin 접근 정책** | admin은 메타데이터만 조회 가능, 파일 내용 접근 불가. 모든 admin 행위는 `audit_logs` 기록 (3.6.4) |
| **그룹 권한** | 파일/폴더 단위 그룹 읽기·쓰기·관리 권한, 하위 폴더 상속, 권한 재정의 지원 |
| **그룹 소유권** | 그룹 소유 파일/폴더는 생성자 퇴사 시에도 그룹 권한 유지, 소유권 이전 가능 |
| **비밀번호** | SCrypt 또는 argon2id, 최소 8자 영숫자+특수문자 |
| **공유 링크** | 비밀번호 옵션, 만료일 설정, 비활성화 즉시 410 반환, 다운로드 횟수 제한 |
| **presigned URL** | 브라우저 발급 ❌, nginx 내부 전용 60초 TTL ✅ |
| **MinIO 버킷 정책** | 익명 접근 완전 차단 (`mc anonymous set none`) — 게이트웨이 모델의 전제 조건 |
| **MinIO 포트** | 9000(API) 호스트 비노출, 9001(콘솔)만 내부 네트워크 |
| **CORS** | 프론트엔드 오리진만 허용, 자격 증명 포함 |
| **데이터 암호화** | 서버측 암호화(MinIO SSE-S3 또는 SSE-KMS), 전송 구간 TLS |
| **속도 제한** | Redis 기반 rate limiting (로그인 5회/분, 업로드 10회/분) |

---

## 11. 모니터링 & 로깅

| 항목 | 도구/방안 |
|---|---|
| **애플리케이션 로깅** | Structured logging (JSON), `loguru` 또는 `structlog` |
| **API 메트릭** | Prometheus + Grafana (FastAPI 미들웨어) |
| **Docker 모니터링** | `docker stats`, Prometheus Node Exporter |
| **알림** | Slack webhook (에러 rate, 스토리지 임계값) |
| **헬스체크** | `/health` 엔드포인트 (DB, MinIO, Redis 연결 상태) |

---

## 12. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| **공유 링크 비활성화 후에도 기존 presigned URL이 TTL 동안 우회** | 보안 사고 | ✅ 게이트웨이 모델 채택 (매 요청 인가), presign은 nginx 내부 전용 60초 TTL |
| **MinIO의 SigV4 서명과 Host 헤더 불일치** | 다운로드 실패 | `proxy_set_header Host minio:9000;` 강제 |
| **Authorization 헤더 MinIO 누수** | 401 multiple auth types 오류 | `proxy_set_header Authorization "";` 덮어쓰기 |
| **대용량 파일 업로드 메모리 과부하** | OOM, 서버 다운 | multipart 스트리밍 업로드(nginx `proxy_request_buffering off`), 최대 10 GB 제한, 타임아웃 설정 |
| **10 GB 업로드 중간 실패 시 전체 재전송** | 사용자 경험 저하, 대역폭 낭비 | 청크 분할 + S3 Multipart Upload 기반 재개 가능 업로드 (3.2, P1) |
| **동시 업로드 시 할당량 검사 레이스** | 할당량 초과 저장 | DB 레벨 원자적 갱신으로 강제 (5.10) |
| **PostgreSQL 단일 장애점(SPOF)** | 서비스 중단 | Phase 2 이후 replication 도입, 정기 백업 (pg_dump + WAL archive) |
| **MinIO 디스크 부족** | 업로드 실패 | 모니터링 + 알림, 자동 확장 스크립트, 사용자별 할당량 강제 |
| **nginx 설정 반영 안 됨 (inode 교체)** | 설정 변경 무효 | `--force-recreate` 또는 디렉터리 마운트 권장 |

---

## 13. 고찰: 기존 설계와의 연계

### 13.1 구글 드라이브 설계에서 채용한 패턴

| 구글 드라이브 설계 | Mini Drive 적용 |
|---|---|
| 블록 저장소 서버 (delta sync) | 범위 제외(3.5) — 업로드 중단/재개는 S3 Multipart 재개 업로드로 대체 |
| 메타데이터 DB (ACID 보장) | PostgreSQL 채택, `file_versions` 테이블로 버전 히스토리 관리 |
| 델타 동기화 전략 | 범위 제외(3.5) — 웹 중심 서비스라 효용 낮음 |
| 파일 버전 이력 (`file_version`) | ✅ `file_versions` 테이블로 동일 패턴 채택 |
| 롱폴링 알림 | 범위 제외(3.5) |

### 13.2 MinIO 개요에서 학습한 교훈 반영

| MinIO 문서 교훈 | Mini Drive 반영 |
|---|---|
| presigned URL의 개별 취소 불가 | ✅ 게이트웨이 모델 채택, 브라우저에 presign 발급 ❌ |
| Host 헤더 SigV4 불일치 | `proxy_set_header Host minio:9000;` 명시 |
| Authorization 헤더 누수 | `proxy_set_header Authorization "";` |
| 단일 파일 bind-mount inode 교체 | `--force-recreate` 또는 디렉터리 마운트 안내 |
| 앱 레벨 버저닝 vs 네이티브 | ✅ 앱 레벨 채택 (DB가 진실 소스) |
| S3 추상화 계층 | ✅ `StorageService` 인터페이스로 MinIO→S3 전환 가능성 확보 |

---

## 14. 비고

- 본 PRD는 **사내 자가 호스팅** 전제이므로, AWS S3/object storage는 추후 마이그레이션 타겟으로 간주
- 모든 secret(JWT, MinIO 암호, DB 패스워드)은 `.env` 파일 또는 Docker Secrets로 관리, 버전 관리에 포함 금지
- Phase 1~4까지 완료 시 사내 시범 운영, Phase 5는 사용 피드백 수집 후 추진
- 사내 폐쇄망 시스템 전제로 SSO 연동은 범위에서 제외 (가입 코드제로 접근 통제)
- 문서화: OpenAPI(Swagger) 자동 생성, README에 Docker Compose 기동 가이드 포함

---

**문서 이력**

| 날짜 | 내용 |
|---|---|
| 2026-07-17 | 초안 작성 |
| 2026-07-18 | 검토 반영: 시스템 admin 설계 추가(3.6, 5.9, 6.7), MinIO 익명 접근 차단, backend 포트 비노출, `files.group_id` DDL 반영, 권한 상속 조회 시 판정으로 변경, 토큰 폐기 전략, 재개 가능 업로드, 할당량 원자적 갱신, 섹션 순서·스택 표 정리 |
| 2026-07-19 | 구현 현행화: 델타 동기화 범위 제외(3.5), 가입 승인제→가입 코드제 전환(3.1, 5.11, 10장), 첫 admin 부트스트랩을 셋업 위저드로 대체(3.6.2, 5.12), setup·signup-codes API(6.1, 6.7), 마일스톤 상태 반영(Phase 1~5 완료, Phase 6 진행 중) |
