r"""``ProjectSnapshot`` -> 마크다운 문자열 변환기.

요구사항(이슈 #9):
    - 디렉토리 트리 섹션과 프로젝트 메타데이터 요약 섹션을 포함한다.
    - 1 MB 초과 파일은 본문 없이 경로/크기만 표시한다.
    - 바이너리 파일도 본문 없이 메타데이터만 표시한다.
    - Ollama 가 생성한 자연어 요약(있을 경우) 을 별도 섹션으로 추가한다.

설계 메모:
    - SRP: 본 모듈은 데이터 -> 텍스트 변환만 담당한다. I/O 는 호출부의 책임.
    - 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

from typing import List, Optional

from sicode.init.scanner import (
    DirectoryEntry,
    FileEntry,
    MetadataFile,
    ProjectSnapshot,
)


#: 코드 블록 안에서 펜스 충돌을 피하기 위해 본문 ``\`\`\``` 를 치환할 마커.
_FENCE_REPLACEMENT: str = "```"


def render_markdown(
    snapshot: ProjectSnapshot,
    *,
    llm_summary: Optional[str] = None,
) -> str:
    """:class:`ProjectSnapshot` 을 마크다운 문자열로 직렬화한다.

    Args:
        snapshot: 정적 스캔 결과.
        llm_summary: Ollama 가 생성한 자연어 요약(없으면 ``None``).

    Returns:
        마크다운 문자열. 끝에 개행이 포함된다.
    """
    lines: List[str] = []
    lines.append("# SICODE Project Snapshot")
    lines.append("")
    lines.append(f"- Root: `{snapshot.root}`")
    lines.append(f"- Max depth: {snapshot.max_depth}")
    lines.append(
        f"- Max inline file size: {_format_bytes(snapshot.max_file_bytes)}"
    )
    lines.append("")

    lines.append("## Directory Tree")
    lines.append("")
    lines.append("```")
    lines.extend(_render_tree_lines(snapshot.tree))
    lines.append("```")
    lines.append("")

    lines.append("## Project Metadata")
    lines.append("")
    if snapshot.metadata_files:
        for meta in snapshot.metadata_files:
            lines.extend(_render_metadata_block(meta))
            lines.append("")
    else:
        lines.append("_No project metadata files detected._")
        lines.append("")

    lines.append("## Skipped & Oversize Files")
    lines.append("")
    skipped = list(_collect_skipped_files(snapshot.tree))
    if skipped:
        for entry in skipped:
            reason = []
            if entry.is_oversize:
                reason.append(f"oversize ({_format_bytes(entry.size_bytes)})")
            if entry.is_binary:
                reason.append("binary")
            reason_str = ", ".join(reason) if reason else "skipped"
            lines.append(f"- `{entry.path}` — {reason_str}")
    else:
        lines.append("_None._")
    lines.append("")

    if llm_summary and llm_summary.strip():
        lines.append("## LLM Summary")
        lines.append("")
        lines.append(llm_summary.strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_tree_lines(node: DirectoryEntry, prefix: str = "") -> List[str]:
    """디렉토리 트리를 아스키 트리 형태로 렌더한다 (루트 한 줄 + 들여쓴 자식)."""
    out: List[str] = []
    if node.depth == 0:
        # 루트 자체는 항상 ``.`` 로 표기.
        out.append(".")
        out.extend(_render_children(node, prefix=""))
    else:  # pragma: no cover - 외부에서 비루트 시작 호출은 사용하지 않음
        out.append(f"{prefix}{node.path}/")
        out.extend(_render_children(node, prefix=prefix + "  "))
    return out


def _render_children(node: DirectoryEntry, prefix: str) -> List[str]:
    """노드의 직속 자식들을 알파벳 순서로 렌더한다."""
    out: List[str] = []
    # 디렉토리 먼저, 그 다음 파일을 표시 (가독성).
    for d in node.directories:
        # 마지막 경로 요소만 트리에 표기.
        name = d.path.split("/")[-1]
        suffix = "/  (truncated)" if d.truncated else "/"
        out.append(f"{prefix}{name}{suffix}")
        if not d.truncated:
            out.extend(_render_children(d, prefix=prefix + "  "))
    for f in node.files:
        name = f.path.split("/")[-1]
        marker = ""
        if f.is_oversize:
            marker = f"  [oversize {_format_bytes(f.size_bytes)}]"
        elif f.is_binary:
            marker = "  [binary]"
        out.append(f"{prefix}{name}{marker}")
    return out


def _render_metadata_block(meta: MetadataFile) -> List[str]:
    """메타데이터 파일 한 개를 마크다운 코드 블록으로 렌더."""
    out: List[str] = []
    truncated_note = " (truncated)" if meta.truncated else ""
    out.append(f"### `{meta.path}`{truncated_note}")
    out.append("")
    fence = _select_fence(meta.content)
    language = _language_hint(meta.path)
    out.append(f"{fence}{language}")
    out.append(meta.content.rstrip("\n"))
    out.append(fence)
    return out


def _select_fence(content: str) -> str:
    r"""본문에 ``\`\`\`` 가 등장하면 한 글자 더 긴 펜스를 사용한다."""
    if _FENCE_REPLACEMENT not in content:
        return _FENCE_REPLACEMENT
    # 본문 안 펜스 길이 + 1 만큼의 백틱을 쓰면 충돌이 나지 않는다.
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(longest + 1, 3)


def _language_hint(path: str) -> str:
    """파일 확장자/이름 기반 코드 블록 언어 힌트(없으면 빈 문자열)."""
    lower = path.lower()
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "markdown"
    if lower.endswith(".rst"):
        return "rst"
    if lower.endswith(".txt"):
        return ""
    if lower.endswith(".cfg") or lower.endswith(".ini"):
        return "ini"
    if lower.endswith(".py"):
        return "python"
    if lower.endswith("/makefile") or lower == "makefile":
        return "make"
    return ""


def _collect_skipped_files(node: DirectoryEntry) -> "list[FileEntry]":
    """트리에서 본문이 생략된(바이너리/오버사이즈) 파일만 수집."""
    out: List[FileEntry] = []
    _walk_files_skipped(node, out)
    out.sort(key=lambda f: f.path)
    return out


def _walk_files_skipped(node: DirectoryEntry, sink: List[FileEntry]) -> None:
    for f in node.files:
        if f.is_oversize or f.is_binary:
            sink.append(f)
    for d in node.directories:
        _walk_files_skipped(d, sink)


def _format_bytes(size: int) -> str:
    """사람이 읽기 좋은 바이트 표시 (KiB/MiB)."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


__all__ = ["render_markdown"]
