r"""``sicode.init.renderer`` 단위 테스트.

검증 범위:
    - 마크다운 결과에 디렉토리 트리 섹션, 메타데이터 섹션이 포함된다.
    - 1 MB 초과 파일은 본문 없이 표기된다.
    - LLM 요약을 인자로 주면 별도 섹션으로 추가된다.
    - 메타데이터 본문에 ``\`\`\`` 가 포함돼도 깨지지 않게 펜스가 확장된다.
"""

from __future__ import annotations

from sicode.init.renderer import render_markdown
from sicode.init.scanner import (
    DirectoryEntry,
    FileEntry,
    MetadataFile,
    ProjectSnapshot,
)


def _make_snapshot(
    *,
    root: str = "/tmp/example",
    files: "tuple[FileEntry, ...]" = (),
    sub_dirs: "tuple[DirectoryEntry, ...]" = (),
    metadata: "tuple[MetadataFile, ...]" = (),
) -> ProjectSnapshot:
    tree = DirectoryEntry(
        path=".",
        depth=0,
        directories=sub_dirs,
        files=files,
        truncated=False,
    )
    return ProjectSnapshot(
        root=root,
        tree=tree,
        metadata_files=metadata,
        max_depth=4,
        max_file_bytes=1024 * 1024,
    )


class TestRendererStructure:
    def test_includes_required_sections(self) -> None:
        snap = _make_snapshot(
            files=(FileEntry(path="main.py", size_bytes=42),),
            metadata=(
                MetadataFile(path="pyproject.toml", content='[project]\nname = "x"'),
            ),
        )
        md = render_markdown(snap)
        assert "# SICODE Project Snapshot" in md
        assert "## Directory Tree" in md
        assert "## Project Metadata" in md
        assert "main.py" in md
        assert "pyproject.toml" in md
        assert "/tmp/example" in md

    def test_renders_directory_tree_with_subdirs(self) -> None:
        sub = DirectoryEntry(
            path="src",
            depth=1,
            files=(FileEntry(path="src/app.py", size_bytes=10),),
        )
        snap = _make_snapshot(sub_dirs=(sub,))
        md = render_markdown(snap)
        # 트리에 src/ 와 app.py 둘 다 등장해야 한다 (디렉토리 라벨에 슬래시 포함).
        assert "src/" in md
        assert "app.py" in md

    def test_metadata_section_when_empty(self) -> None:
        snap = _make_snapshot()
        md = render_markdown(snap)
        assert "_No project metadata files detected._" in md

    def test_skipped_section_lists_oversize_and_binary(self) -> None:
        snap = _make_snapshot(
            files=(
                FileEntry(
                    path="huge.log",
                    size_bytes=2 * 1024 * 1024,
                    is_binary=False,
                    is_oversize=True,
                ),
                FileEntry(
                    path="data.bin",
                    size_bytes=100,
                    is_binary=True,
                    is_oversize=False,
                ),
                FileEntry(path="ok.py", size_bytes=10),
            )
        )
        md = render_markdown(snap)
        assert "huge.log" in md
        assert "oversize" in md
        assert "data.bin" in md
        assert "binary" in md


class TestRendererLLMSummary:
    def test_includes_llm_summary_when_provided(self) -> None:
        snap = _make_snapshot()
        md = render_markdown(snap, llm_summary="This is a Python CLI.")
        assert "## LLM Summary" in md
        assert "This is a Python CLI." in md

    def test_omits_llm_summary_when_none(self) -> None:
        snap = _make_snapshot()
        md = render_markdown(snap, llm_summary=None)
        assert "## LLM Summary" not in md

    def test_omits_llm_summary_when_blank(self) -> None:
        snap = _make_snapshot()
        md = render_markdown(snap, llm_summary="   \n  ")
        # 공백만 있는 요약은 호출부 정규화 외에도 렌더러 스스로 섹션을 만들지 않는다.
        assert "## LLM Summary" not in md


class TestRendererFenceEscaping:
    def test_metadata_content_with_backticks_does_not_break_fences(self) -> None:
        # 메타데이터 본문 안에 ``` 가 포함되면 펜스를 더 길게 만든다.
        meta = MetadataFile(
            path="README.md",
            content="example: ```python\nprint('x')\n```\n",
        )
        snap = _make_snapshot(metadata=(meta,))
        md = render_markdown(snap)
        # 외곽 펜스는 본문보다 더 긴 백틱을 사용해야 한다.
        assert "````" in md  # 4-백틱 펜스
        # 본문이 그대로 보존되어 있어야 한다.
        assert "print('x')" in md
