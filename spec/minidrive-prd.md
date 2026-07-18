# Mini Drive – 사내 파일 공유/관리 서비스 PRD

**작성일:** 2026-07-17  
**저자:** (미정)  
**검토자:** (미정)  
**상태:** Phase 1~5 구현 완료, Phase 6(셋업 위저드 + 가입 코드제) 구현 중, Phase 7(LLM 위키 & 챗봇) 설계 확정

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

### 3.7 LLM 위키 & 챗봇 (LLM Wiki, Phase 7)

드라이브에 축적된 사내 문서를 지식원으로 하는 **LLM 위키 + 챗봇**. Karpathy의 LLM Wiki 패턴([gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), 2026-04)과 권한 인지 RAG(permission-aware RAG)를 결합한 하이브리드다. 대원칙은 하나 — **사용자는 자신의 접근 권한 안에 있는 데이터로부터 나온 답변만 받는다.**

#### 3.7.1 설계 원칙 — Karpathy 패턴 × 권한 경계

Karpathy 패턴의 요지는 "전통적 RAG는 매 질문마다 원문에서 지식을 처음부터 재발견하지만, LLM이 마크다운 위키를 점진적으로 유지·성장시키면 지식이 **컴파일된 산출물**로 축적된다"는 것이다. 3계층·3연산을 Mini Drive에 다음과 같이 매핑한다.

| Karpathy 3계층 | Mini Drive 매핑 |
|---|---|
| **Raw sources** (불변 원문) | 드라이브 파일. LLM은 원문을 절대 수정하지 않으며, 버전은 `file_versions`가 보존 |
| **The wiki** (LLM 생성 마크다운) | 위키 스페이스의 `.md` 페이지 — **일반 드라이브 파일로 저장**하여 기존 권한·버전·미리보기 체계를 그대로 재사용 (`index.md` 카탈로그, `log.md` append-only 기록 포함) |
| **The schema** (구조·워크플로 정의) | 스페이스 설정(`wiki_spaces.settings`) + 컴파일 지침 페이지 |

| 연산 | 동작 |
|---|---|
| **Ingest** | 소스 등록/버전 갱신 시: Upstage Document Parse로 텍스트 추출 → 청크 분할·임베딩(`file_chunks`, RAG 인덱스) → LLM이 관련 위키 페이지 생성/갱신 + 상호링크·`index.md`·`log.md` 갱신 |
| **Query** | 챗봇 질문 → 권한 내 검색(위키 페이지 우선 + 원문 청크 보강) → 출처 인용 답변. 가치 있는 답변은 사용자가 위키 페이지로 승격 가능 |
| **Lint** | 정기/트리거 헬스체크: 고아 페이지, 깨진 링크, 낡은 소스(버전 갱신·권한 회수) 탐지 → 재컴파일 또는 페이지 제거 |

**핵심 불변식 — 권한 경계 = 컴파일 경계.** 위키 스페이스는 `personal`(개인) 또는 `group`(그룹) 스코프를 가지며, 컴파일에 투입되는 소스는 **해당 스코프가 read 가능한 파일만**이다. 컴파일된 페이지는 스코프 소유 폴더(개인 소유 또는 `group_id` 소유)에 저장되므로, 페이지를 읽을 수 있는 사람 = 그 소스들을 읽을 수 있는 사람이 구조적으로 보장된다. 스코프를 가로지르는 컴파일(예: A그룹 문서 + B그룹 문서를 한 페이지로 종합)은 금지한다.

#### 3.7.2 기능 요구사항

| 기능 | 설명 | 우선순위 |
|---|---|---|
| **위키 스페이스 생성** | personal/group 스코프 지정, 위키 페이지가 저장될 루트 폴더 자동 생성 | P0 |
| **소스 등록** | 파일 또는 폴더(하위 재귀)를 스페이스 소스로 등록 → Ingest 잡 큐잉. 스코프가 read 불가한 파일은 등록 거부 | P0 |
| **자동 재인덱싱** | 소스 파일 업로드/버전 갱신/삭제 훅 → 해당 청크·위키 페이지 stale 마킹 + 재인덱싱 잡 | P0 |
| **챗봇 질의** | 자연어 질문 → 권한 인지 검색 → 출처 인용(파일 링크) 포함 답변, SSE 스트리밍 | P0 |
| **대화 세션** | 세션 생성/목록/이어가기, 히스토리 저장 | P1 |
| **위키 브라우징** | 위키 페이지 마크다운 렌더링(기존 미리보기 확장), 상호링크 탐색 | P1 |
| **답변 → 위키 승격** | 챗봇 답변을 스페이스의 위키 페이지로 저장 (Karpathy Query 연산의 "가치 있는 답변은 위키가 된다") | P1 |
| **Lint 실행** | 수동/스케줄 헬스체크, 결과 리포트 | P1 |
| **근거성 검증** | Upstage Groundedness Check로 답변-컨텍스트 사실 일치 검증, 불일치 시 경고 표시 | P2 |
| **인덱싱 제외 플래그** | 민감 폴더/파일을 외부 API 전송·인덱싱 대상에서 제외 | P2 |

#### 3.7.3 권한 인지 검색 (Permission-aware Retrieval)

RAG는 접근 제어를 기본 제공하지 않는다 — 벡터 DB에 데이터가 있으면 권한 없는 사용자의 질문에도 답이 나갈 수 있다. 이를 **2단계 필터**로 차단한다.

| 단계 | 방식 |
|---|---|
| **① 사전 필터 (coarse)** | 벡터 검색 SQL에 후보 파일 집합 조건 결합: `소유 파일 ∪ 내 그룹에 부여된 서브트리` (PermissionService가 산출). pgvector 쿼리가 이 집합 밖의 청크를 아예 스캔하지 않음 |
| **② 사후 검증 (exact)** | top-k 결과의 파일 각각을 기존 단일 관문 `get_access_level`로 재검증 — 상속 재정의·`expires_at` 만료·회수 직후 캐시 무효화까지 정확 반영. 통과한 청크만 LLM 컨텍스트에 투입 |

- 검색·생성은 **항상 요청 사용자 본인 자격**으로 수행한다. 서비스 계정이 전체 인덱스를 대신 뒤지는 구조 금지.
- 답변/검색 결과 캐시는 **사용자 단위로만** 허용 — 사용자 간 공유 캐시는 권한 우회 경로가 된다.
- **admin도 예외 없음**: 3.6.4 접근 정책 그대로 — admin은 위키/챗봇을 통해서도 타인 파일 내용에 접근할 수 없으며, 운영 메트릭(잡 큐 상태, 인덱스 규모)만 조회한다.
- 권한 회수 시: 기존 권한 캐시 무효화(세대 카운터)에 더해, 회수된 소스를 쓰는 위키 페이지를 stale 마킹 → Lint가 해당 소스 제외 재컴파일 또는 페이지 제거.

#### 3.7.4 LLM 스택 & 모델 라우팅

**LangChain 1.x + LangGraph**(오케스트레이션·체크포인터) 기반. 사용 가능한 자격: 사내 vLLM 서버(GLM 5.2, OpenAI 호환 엔드포인트), Upstage API key, OpenAI API key.

| 역할 | 기본 | 대안 | 비고 |
|---|---|---|---|
| **챗 생성** | GLM 5.2 — 사내 vLLM, `ChatOpenAI(base_url=...)` 연결 | OpenAI / ChatUpstage(Solar) | 기본을 사내 vLLM으로 두어 문서 컨텍스트의 외부 전송 최소화 |
| **문서 파싱** | Upstage Document Parse (`UpstageDocumentParseLoader`) — PDF/오피스/스캔 OCR, 표 보존 | 텍스트·마크다운·코드는 직접 추출 | |
| **임베딩** | Upstage `solar-embedding-1-large` (4096차원, 한국어 강점) | OpenAI `text-embedding-3-small` (1536차원) | 임베딩 모델은 배포 시 고정 — 교체 시 전체 재임베딩 필요 |
| **근거성 검증** | `UpstageGroundednessCheck` | 생략 가능 (P2) | |

- 벡터 스토어: `langchain-postgres`의 `PGVector` — 기존 PostgreSQL에 pgvector 확장만 추가, SQL 조인으로 권한 사전 필터를 벡터 검색에 직접 결합.
- Ingest/컴파일은 비동기 워커(arq + 기존 Redis 큐, 별도 `worker` 컨테이너)가 수행 — 업로드 경로의 응답 시간에 영향 없음.
- **egress 주의**: 파싱·임베딩에 외부 API를 쓰면 문서 내용이 사외로 전송된다. 인덱싱 제외 플래그(3.7.2)로 통제하고, 완전 폐쇄가 필요해지면 vLLM에 임베딩 모델을 추가 배포해 대체한다.

#### 3.7.5 챗봇 파이프라인

```
질문 (SSE 연결)
  → 쿼리 임베딩 (Upstage)
  → 후보 파일 집합 산출 (PermissionService, SQL)
  → pgvector top-k: 위키 페이지 우선 + 원문 청크 보강 (하이브리드)
  → 사후 권한 검증 (get_access_level, 탈락 청크 폐기)
  → GLM 5.2 생성 — 출처 인용 [파일명](링크) 강제
  → (P2) Groundedness Check
  → SSE 스트리밍 응답 + citations 저장
```

컨텍스트로 투입되는 파일 내용은 **데이터로만 취급**한다(프롬프트 인젝션 방어): 시스템 프롬프트와 구분자 분리, 답변 체인에는 도구 호출 권한을 부여하지 않는다.

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
| | pgvector (Phase 7부터 필수 — RAG 벡터 인덱스) | 최신 |
| **Cache / Token Store** | Redis | 7.x |
| **LLM / RAG (Phase 7)** | LangChain / LangGraph | 1.x |
| | langchain-upstage (Document Parse, Solar Embedding, Groundedness Check) | 최신 |
| | langchain-postgres (`PGVector`) | 최신 |
| | vLLM — 사내 GPU 서버의 GLM 5.2, OpenAI 호환 엔드포인트 | 외부 시스템 |
| | arq (Redis 기반 비동기 워커) | 최신 |
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

### 5.13 file_chunks 테이블 (RAG 인덱스, Phase 7)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE file_chunks (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    version         INT NOT NULL,               -- 인덱싱된 파일 버전 (버전 갱신 시 stale 판정 기준)
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(4096),               -- solar-embedding-1-large 기준. OpenAI 사용 시 1536
    token_count     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (file_id, version, chunk_index)
);
```

- 청크에는 **권한 정보를 비정규화하지 않는다** — 권한은 조회 시 `files` 조인 + `get_access_level`로 판정 (5.7과 동일 철학: 조회 시 판정이라 권한 변경이 즉시 반영).
- 인덱스: pgvector HNSW는 2,000차원(halfvec 4,000) 제한이 있어 4096차원은 인덱스 없이 정확 검색한다. 사내 규모(수십만 청크 이하)에서는 사전 필터로 후보가 좁혀져 충분하며, 초과 시 1536차원 모델로 전환 + HNSW 추가.
- 위키 페이지(`.md` 드라이브 파일)도 동일하게 청크·임베딩되어 검색 대상이 된다.

### 5.14 wiki_spaces / wiki_sources 테이블 (Phase 7)

```sql
CREATE TABLE wiki_spaces (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    scope           VARCHAR(20) NOT NULL,       -- personal / group
    user_id         BIGINT REFERENCES users(id),   -- personal 스코프의 소유자
    group_id        BIGINT REFERENCES groups(id),  -- group 스코프의 소유 그룹
    root_folder_id  BIGINT NOT NULL REFERENCES files(id),  -- 위키 페이지가 저장되는 드라이브 폴더
    settings        JSONB NOT NULL DEFAULT '{}',   -- 컴파일 지침, 자동 ingest 여부, 제외 패턴
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((scope = 'personal' AND user_id IS NOT NULL AND group_id IS NULL)
        OR (scope = 'group'    AND group_id IS NOT NULL AND user_id IS NULL))
);

CREATE TABLE wiki_sources (
    space_id        BIGINT NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,  -- 파일 또는 폴더(재귀)
    recursive       BOOLEAN NOT NULL DEFAULT TRUE,  -- 폴더일 때 하위 포함
    status          VARCHAR(20) NOT NULL DEFAULT 'queued',  -- queued / indexed / stale / failed
    last_ingested_version INT,
    added_by        BIGINT NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (space_id, file_id)
);
```

- 소스 등록 시 **스코프의 read 권한을 검증**한다(권한 경계 = 컴파일 경계, 3.7.1). group 스코프면 그룹이 해당 파일에 read 이상을 가져야 등록 가능.
- 위키 페이지는 별도 테이블이 아니라 `root_folder_id` 하위의 일반 드라이브 파일 — 권한·버전·휴지통 모두 기존 체계.

### 5.15 wiki_jobs 테이블 (비동기 워커 큐, Phase 7)

```sql
CREATE TABLE wiki_jobs (
    id              BIGSERIAL PRIMARY KEY,
    space_id        BIGINT NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE,
    file_id         BIGINT REFERENCES files(id) ON DELETE SET NULL,
    kind            VARCHAR(20) NOT NULL,       -- ingest / compile / lint
    status          VARCHAR(20) NOT NULL DEFAULT 'queued',  -- queued / running / done / failed
    retries         INT NOT NULL DEFAULT 0,     -- 최대 3회 재시도 후 failed
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

실행 큐 자체는 arq(Redis)가 담당하고, 이 테이블은 이력·상태 조회·재시도 판단의 진실 소스다.

### 5.16 chat_sessions / chat_messages 테이블 (Phase 7)

```sql
CREATE TABLE chat_sessions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id        BIGINT REFERENCES wiki_spaces(id) ON DELETE SET NULL,  -- NULL = 전체 접근 범위 대상
    title           VARCHAR(200) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,       -- user / assistant
    content         TEXT NOT NULL,
    citations       JSONB,                      -- [{file_id, chunk_id, version, snippet}]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

세션·메시지는 소유 사용자만 조회 가능(공유 불가). `citations`의 파일 링크는 클릭 시점에 다시 `ensure_file_access`를 통과한다 — 대화 이후 권한이 회수된 파일은 열리지 않는다.

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

### 6.8 LLM 위키 (Phase 7)

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/wiki/spaces` | 스페이스 생성 (name, scope, group_id — group 스코프는 그룹 admin 이상만) |
| `GET`  | `/api/wiki/spaces` | 내가 접근 가능한 스페이스 목록 (personal 소유 + 소속 그룹) |
| `GET`  | `/api/wiki/spaces/{id}` | 상세 — `index.md` 요약, 소스 목록·상태, 최근 `log.md` 항목 |
| `POST` | `/api/wiki/spaces/{id}/sources` | 소스 등록 (file_id, recursive) → 스코프 read 검증 후 Ingest 잡 큐잉 |
| `DELETE` | `/api/wiki/spaces/{id}/sources/{fileId}` | 소스 제거 → 관련 청크 삭제 + 위키 페이지 stale 마킹 |
| `POST` | `/api/wiki/spaces/{id}/lint` | Lint 실행 (수동 트리거) |
| `GET`  | `/api/wiki/spaces/{id}/jobs` | Ingest/컴파일/Lint 잡 상태 조회 |
| `DELETE` | `/api/wiki/spaces/{id}` | 스페이스 삭제 (청크·잡 삭제; 위키 페이지 폴더는 드라이브에 잔존, 소유자 선택 삭제) |

> 위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/이름 변경은 기존 6.2 파일 API를 그대로 쓴다.

### 6.9 챗봇 (Phase 7)

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/api/chat/sessions` | 세션 생성 (space_id 옵션 — 미지정 시 내 전체 접근 범위 대상) |
| `GET`  | `/api/chat/sessions` | 내 세션 목록 |
| `GET`  | `/api/chat/sessions/{id}` | 세션 메시지 히스토리 (citations 포함) |
| `POST` | `/api/chat/sessions/{id}/messages` | 질문 전송 → **SSE 스트리밍** 답변 (권한 인지 검색 3.7.3 파이프라인) |
| `POST` | `/api/chat/messages/{id}/promote` | 답변을 스페이스 위키 페이지로 승격 (space_id 필수, 스코프 쓰기 권한 검증) |
| `DELETE` | `/api/chat/sessions/{id}` | 세션 삭제 |

> 챗봇·위키 API 전체가 `get_current_user` 인가를 통과하며, 검색·생성은 항상 요청 사용자 자격으로 실행된다. admin 전용 운영 조회(잡 큐 적체, 인덱스 규모)는 `/api/admin/stats`에 필드로 추가한다 — 내용 접근 엔드포인트는 두지 않는다.

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

각 페이즈 완료 시 검토·컨펌 후 다음 페이즈로 진행한다. **현재 상태: Phase 1~5 완료(각 페이즈 게이트웨이 E2E 통과), Phase 6 구현 중(백엔드 진행 중, 프론트 예정), Phase 7 설계 확정(미착수).**

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

### Phase 7: LLM 위키 & 챗봇 (3-4주) 📋 설계 확정

| 항목 | 내용 |
|---|---|
| **목표** | 권한 인지 RAG + Karpathy LLM Wiki 패턴의 사내 지식 위키·챗봇 (3.7) |
| **범위** | **7-1 인덱싱 기반**: pgvector 확장·`file_chunks`(5.13), arq `worker` 컨테이너(compose 추가), Upstage Document Parse 파싱→청크→임베딩 파이프라인, 업로드/버전/삭제 훅 재인덱싱, 인덱싱 제외 플래그 · **7-2 챗봇**: 권한 인지 검색(사전 SQL 필터 + `get_access_level` 사후 검증), LangGraph 파이프라인, GLM 5.2(vLLM) 연결, SSE 스트리밍, 세션/히스토리(5.16), 챗 UI · **7-3 위키**: `wiki_spaces`/`wiki_sources`/`wiki_jobs`(5.14~5.15), Ingest 컴파일(위키 페이지=드라이브 파일), 답변 승격, Lint, 위키 브라우징 UI |
| **완료 조건** | ① A그룹 문서로 컴파일된 위키에 B그룹 사용자가 질문 → 해당 내용 검색·인용 불가 E2E ② 권한 회수 직후 동일 질문에서 해당 소스 즉시 제외 ③ 소스 버전 갱신 → 재인덱싱 → 새 내용으로 답변 ④ 출처 인용 링크가 `ensure_file_access` 통과 후에만 열림 ⑤ admin 계정으로 타인 파일 내용이 챗봇 경유로도 조회 불가 |
| **제외** | 실시간 협업 편집, 외부 공개 위키(공유 링크로 위키 페이지 공유는 기존 3.4로 가능), 멀티모달(이미지 내용 이해), 에이전틱 도구 호출 |

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
| **속도 제한** | Redis 기반 rate limiting (로그인 5회/분, 업로드 10회/분, 챗봇 질의 제한은 Phase 7에서 추가) |
| **LLM 검색 인가 (Phase 7)** | 벡터 검색은 사전 SQL 필터 + `get_access_level` 사후 검증 2중 방어. 검색·생성은 항상 요청 사용자 자격, 사용자 간 캐시 공유 금지, admin도 내용 접근 불가 (3.7.3) |
| **LLM 데이터 egress (Phase 7)** | 챗 생성 기본은 사내 vLLM(GLM 5.2). 외부 API(Upstage 파싱·임베딩, OpenAI)로 전송되는 범위는 인덱싱 제외 플래그로 통제 |
| **프롬프트 인젝션 (Phase 7)** | 파일 내용은 데이터로만 취급 — 시스템 프롬프트 분리, 답변 체인에 도구 호출 미부여, 출처 인용 강제 (3.7.5) |

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
| **벡터 인덱스에 권한 없는 데이터 노출 (Phase 7)** | 권한 우회 정보 유출 | 청크에 권한 비정규화 ❌ → 조회 시 사전 SQL 필터 + `get_access_level` 사후 검증 (3.7.3). 완료 조건에 교차 그룹 차단 E2E 포함 |
| **소스 권한 회수 후 컴파일된 위키 페이지에 지식 잔존 (Phase 7)** | 회수 이전 지식의 간접 노출 | 권한 회수 이벤트 → 해당 페이지 stale 마킹 → Lint가 소스 제외 재컴파일/제거. 잔존 창은 Lint 주기로 상한 |
| **외부 API로 문서 내용 전송 (Phase 7)** | 기밀 유출 | 챗 생성 기본 사내 vLLM, 민감 폴더 인덱싱 제외 플래그, 필요 시 vLLM 임베딩 모델 배포로 완전 사내화 |
| **임베딩 인덱스 낡음 — 버전 갱신 미반영 (Phase 7)** | 낡은 답변 | 업로드/버전/삭제 훅에서 stale 마킹 + 재인덱싱 잡 자동 큐잉, `version` 컬럼으로 판정 |
| **LLM 응답의 사실 불일치(할루시네이션) (Phase 7)** | 잘못된 사내 정보 확산 | 출처 인용 강제 + Upstage Groundedness Check(P2), 위키 승격은 사용자 검토 후 수동 |

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

### 13.3 Karpathy LLM Wiki 패턴 적용 (Phase 7)

Karpathy가 2026-04 공개한 [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)의 패턴을 채용하되, 단일 사용자 개인 지식 도구인 원안을 **다중 사용자·권한 경계** 환경에 맞게 변형했다.

| 원안 (Karpathy gist) | Mini Drive 적용 |
|---|---|
| Raw sources = 로컬 파일, 인간이 큐레이션 | ✅ 드라이브 파일 + `wiki_sources` 등록. "인간은 소스 큐레이션·질문, LLM은 부기·유지보수" 철학 유지 |
| Wiki = 로컬 마크다운 + Obsidian 브라우징 | 변형 — 위키 페이지를 **드라이브 파일**로 저장해 권한·버전·미리보기 재사용. 브라우징은 자체 마크다운 렌더 |
| 단일 사용자 — 권한 개념 없음 | **변형 (핵심)** — 스페이스 스코프(personal/group)로 "권한 경계 = 컴파일 경계" 불변식 추가 (3.7.1) |
| 순수 위키 (RAG 대체) | 하이브리드 — 위키 페이지 우선 + 원문 청크 RAG 보강. 파생 구현체들(nashsu/llm_wiki, lucasastorian/llmwiki)도 원문 인덱스를 인용용으로 유지하는 동일 결론 |
| Ingest / Query / Lint 3연산 | ✅ 동일 채택. Lint에 권한 회수 감지(stale 마킹) 역할 추가 |
| CLI/에이전트가 수동 실행 | 변형 — arq 워커가 업로드/버전 훅으로 자동 실행 |

**조사 참고 자료** (2026-07 기준):
- 원 출처: [Karpathy llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- 파생 구현체: [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) (~14.8k★, 그래프 연관도 모델·증분 캐싱), [lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki) (VaultFS 저장 추상화·MCP), [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) (gist를 Agent Skill로 코드화)
- 권한 인지 RAG: [Oso — Authorization in RAG](https://www.osohq.com/post/right-approach-to-authorization-in-rag), [Cerbos — RAG Access Control](https://cerbos.dev/blog/access-control-for-rag-llms), [AWS Security Blog — Authorizing access to data with RAG](https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/)
- LangChain 통합: [vLLM(OpenAI 호환) 연결](https://docs.langchain.com/oss/python/integrations/chat/vllm), [Upstage 통합(langchain-upstage)](https://docs.langchain.com/oss/python/integrations/providers/upstage)

---

## 14. 비고

- 본 PRD는 **사내 자가 호스팅** 전제이므로, AWS S3/object storage는 추후 마이그레이션 타겟으로 간주
- 모든 secret(JWT, MinIO 암호, DB 패스워드)은 `.env` 파일 또는 Docker Secrets로 관리, 버전 관리에 포함 금지
- Phase 1~4까지 완료 시 사내 시범 운영, Phase 5는 사용 피드백 수집 후 추진
- 사내 폐쇄망 시스템 전제로 SSO 연동은 범위에서 제외 (가입 코드제로 접근 통제)
- Phase 7의 외부 LLM API(Upstage, OpenAI) 사용은 폐쇄망 전제의 **승인된 예외**다 — 전송 범위는 인덱싱 제외 플래그로 통제하며, 완전 사내화가 필요해지면 vLLM에 임베딩 모델을 추가 배포한다 (3.7.4)
- 문서화: OpenAPI(Swagger) 자동 생성, README에 Docker Compose 기동 가이드 포함

---

**문서 이력**

| 날짜 | 내용 |
|---|---|
| 2026-07-17 | 초안 작성 |
| 2026-07-18 | 검토 반영: 시스템 admin 설계 추가(3.6, 5.9, 6.7), MinIO 익명 접근 차단, backend 포트 비노출, `files.group_id` DDL 반영, 권한 상속 조회 시 판정으로 변경, 토큰 폐기 전략, 재개 가능 업로드, 할당량 원자적 갱신, 섹션 순서·스택 표 정리 |
| 2026-07-19 | 구현 현행화: 델타 동기화 범위 제외(3.5), 가입 승인제→가입 코드제 전환(3.1, 5.11, 10장), 첫 admin 부트스트랩을 셋업 위저드로 대체(3.6.2, 5.12), setup·signup-codes API(6.1, 6.7), 마일스톤 상태 반영(Phase 1~5 완료, Phase 6 진행 중) |
