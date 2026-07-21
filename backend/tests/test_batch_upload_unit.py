"""배치 업로드 경로 정규화 단위 테스트 (DB/MinIO 불필요).

normalize_relpath 는 배치 업로드의 **유일한 신뢰 경계**다. 여기서 통과한 세그먼트가 그대로
폴더 이름과 파일명이 되므로, 경로 탈출·제어문자·길이/깊이 초과를 전부 여기서 막아야 한다.
프론트엔드 lib/fileTree.ts 의 검증도 같은 규칙을 따라야 한다.
"""

from __future__ import annotations

import pytest

from app.services.files import (
    MAX_NAME_LENGTH,
    MAX_PATH_DEPTH,
    MAX_PATH_LENGTH,
    FileServiceError,
    normalize_relpath,
)


def _reject(path: str) -> FileServiceError:
    with pytest.raises(FileServiceError) as excinfo:
        normalize_relpath(path)
    assert excinfo.value.status_code == 422
    return excinfo.value


class TestAccepts:
    def test_plain_file(self) -> None:
        assert normalize_relpath("a.png") == ["a.png"]

    def test_nested(self) -> None:
        assert normalize_relpath("docs/img/a.png") == ["docs", "img", "a.png"]

    def test_backslash_is_normalized(self) -> None:
        # Windows 클라이언트가 보내는 구분자.
        assert normalize_relpath("docs\\img\\a.png") == ["docs", "img", "a.png"]

    def test_redundant_separators_collapse(self) -> None:
        assert normalize_relpath("docs//img/./a.png") == ["docs", "img", "a.png"]

    def test_surrounding_whitespace_trimmed(self) -> None:
        assert normalize_relpath("  docs  /  a.png  ") == ["docs", "a.png"]

    def test_unicode_names(self) -> None:
        assert normalize_relpath("사진/여행/한라산.jpg") == ["사진", "여행", "한라산.jpg"]

    def test_leading_dots_are_not_traversal(self) -> None:
        # ".." 만 탈출이고, "..foo"/".hidden" 은 정상 이름이다.
        assert normalize_relpath("..foo/.hidden") == ["..foo", ".hidden"]

    def test_at_depth_limit(self) -> None:
        path = "/".join("d" for _ in range(MAX_PATH_DEPTH))
        assert len(normalize_relpath(path)) == MAX_PATH_DEPTH

    def test_at_name_length_limit(self) -> None:
        name = "n" * MAX_NAME_LENGTH
        assert normalize_relpath(name) == [name]


class TestRejectsTraversal:
    def test_parent_segment(self) -> None:
        _reject("../etc/passwd")

    def test_parent_segment_in_middle(self) -> None:
        # 상쇄 계산 없이 거부한다 — 정규화하면 "b" 가 되지만 의도가 불온하다.
        _reject("a/../b")

    def test_parent_segment_via_backslash(self) -> None:
        _reject("..\\..\\etc\\passwd")

    def test_absolute_path(self) -> None:
        _reject("/etc/passwd")

    def test_windows_drive(self) -> None:
        _reject("C:/Windows/system32")

    def test_windows_drive_lowercase(self) -> None:
        _reject("d:\\data\\x.txt")


class TestRejectsBadNames:
    def test_control_char(self) -> None:
        _reject("bad\x01name.txt")

    def test_nul_byte(self) -> None:
        _reject("bad\x00name.txt")

    def test_del_char(self) -> None:
        _reject("bad\x7fname.txt")

    def test_newline(self) -> None:
        _reject("bad\nname.txt")

    def test_whitespace_only_segment(self) -> None:
        _reject("docs/   /a.png")

    def test_empty_path(self) -> None:
        _reject("")

    def test_separators_only(self) -> None:
        _reject("///")


class TestRejectsOverLimits:
    def test_depth_over_limit(self) -> None:
        _reject("/".join("d" for _ in range(MAX_PATH_DEPTH + 1)))

    def test_name_over_limit(self) -> None:
        _reject("n" * (MAX_NAME_LENGTH + 1))

    def test_path_over_limit(self) -> None:
        # 길이 검사가 깊이/이름 검사보다 먼저 걸린다.
        _reject("a/" * (MAX_PATH_LENGTH // 2 + 1))


class TestMessages:
    """사용자에게 그대로 노출되는 문구 — 프론트 사전 검사와 대응한다."""

    def test_traversal_message(self) -> None:
        assert "상위 경로" in _reject("a/../b").detail

    def test_depth_message(self) -> None:
        detail = _reject("/".join("d" for _ in range(MAX_PATH_DEPTH + 1))).detail
        assert "깊이" in detail

    def test_control_char_message(self) -> None:
        assert "쓸 수 없는 문자" in _reject("bad\x01name.txt").detail

    def test_name_length_message(self) -> None:
        assert "255자" in _reject("n" * (MAX_NAME_LENGTH + 1)).detail
