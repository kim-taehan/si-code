"""``/init`` 슬래시 명령 구현.

요구사항(이슈 #9):
    - ``/init`` 입력 시 :func:`Path.cwd` 를 루트로 정적 분석 결과를 모아 마크다운으로 저장.
    - 결과 파일 기본 경로는 ``<cwd>/SICODE.md``. 이미 존재하면 ``SICODE.md.bak`` 로 백업.
    - 백업이 발생하면 그 사실을 REPL 출력에 포함, 저장 후에는 절대 경로를 출력.
    - Ollama 가 가용하면 자연어 요약을 추가하고, 실패하면 정적 분석 결과만으로 정상 완료.
    - 인자는 받지 않는다.

설계 메모:
    - DIP: scanner / renderer / Ollama summarizer / file writer 를 모두 콜러블로 주입.
      기본값은 모듈 수준 헬퍼이다. 테스트는 mock 으로 교체한다.
    - SRP: 본 클래스는 "스캔 -> 렌더 -> 저장 -> 출력 문자열 조립" 의 흐름 제어만 담당.
      파일 시스템 조작은 :func:`write_snapshot_file` 로 분리해 단위 테스트가 용이하다.
    - OCP: 새 명령 추가 시 ``register_default_commands`` 한 줄만 변경된다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.init.renderer import render_markdown
from sicode.init.scanner import ProjectSnapshot, scan_project


#: 결과 마크다운 기본 파일 이름.
DEFAULT_OUTPUT_FILENAME: str = "SICODE.md"

#: 백업 파일 이름(``SICODE.md`` 가 이미 존재할 때 사용).
DEFAULT_BACKUP_SUFFIX: str = ".bak"


class ProjectSummarizer(Protocol):
    """:class:`ProjectSnapshot` -> 자연어 요약 텍스트 변환자.

    Ollama 가 꺼져 있거나 오류가 발생할 수 있어 호출자는 반드시 예외를 핸들링한다.
    예외는 어떤 종류든 상위에서 잡아 무시한다(요약 없이 정상 완료).
    """

    def __call__(self, snapshot: ProjectSnapshot) -> str:  # pragma: no cover - 프로토콜
        ...


@dataclass(frozen=True)
class WriteResult:
    """파일 저장 결과.

    Attributes:
        output_path: 저장된 마크다운 파일의 절대 경로.
        backup_path: 백업이 만들어졌으면 그 절대 경로, 없으면 ``None``.
    """

    output_path: Path
    backup_path: Optional[Path]


def write_snapshot_file(
    markdown: str,
    output_path: Path,
    *,
    backup_suffix: str = DEFAULT_BACKUP_SUFFIX,
) -> WriteResult:
    """마크다운을 ``output_path`` 에 저장한다. 이미 파일이 있으면 백업한다.

    백업은 ``output_path`` 와 같은 디렉토리에서 같은 파일명에 ``backup_suffix`` 를
    덧붙인 경로로 생성된다(기존 백업이 있으면 덮어쓴다 — 단일 백업만 유지).

    Args:
        markdown: 저장할 본문.
        output_path: 저장 경로(절대 권장).
        backup_suffix: 백업 파일에 덧붙일 접미사. 기본값 ``".bak"``.

    Returns:
        :class:`WriteResult`.
    """
    backup_path: Optional[Path] = None
    if output_path.exists():
        backup_path = output_path.with_name(output_path.name + backup_suffix)
        # 기존 백업이 있으면 덮어쓴다(단일 백업 유지). 디렉토리/심볼릭 등 예외적
        # 케이스는 그대로 OSError 가 전파되도록 둔다 — 사용자에게 메시지로 노출 가능.
        shutil.copy2(str(output_path), str(backup_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return WriteResult(output_path=output_path, backup_path=backup_path)


#: 기본 scanner / renderer / writer 시그니처. 테스트에서는 mock 으로 교체된다.
ScannerFn = Callable[[Path], ProjectSnapshot]
RendererFn = Callable[[ProjectSnapshot, Optional[str]], str]
WriterFn = Callable[[str, Path], WriteResult]
CwdFn = Callable[[], Path]


def _default_scanner(root: Path) -> ProjectSnapshot:
    return scan_project(root)


def _default_renderer(snapshot: ProjectSnapshot, llm_summary: Optional[str]) -> str:
    return render_markdown(snapshot, llm_summary=llm_summary)


def _default_writer(markdown: str, output_path: Path) -> WriteResult:
    return write_snapshot_file(markdown, output_path)


class InitCommand(SlashCommand):
    """현재 디렉토리를 정적 분석해 ``SICODE.md`` 로 저장하는 슬래시 명령.

    DI 가능한 협력자:
        scanner: ``Path`` -> :class:`ProjectSnapshot`.
        renderer: ``(snapshot, llm_summary)`` -> 마크다운 문자열.
        writer: ``(markdown, output_path)`` -> :class:`WriteResult`.
        summarizer: 선택적 자연어 요약기. 예외는 본 클래스가 잡아 무시한다.
        cwd_fn: 작업 디렉토리 제공자(테스트에서 ``tmp_path`` 주입용).
        output_filename: 결과 파일 이름. 기본 ``SICODE.md``.
    """

    name: str = "init"
    aliases: "tuple[str, ...]" = ()
    description: str = "Scan current directory and save context to SICODE.md."

    def __init__(
        self,
        *,
        scanner: ScannerFn = _default_scanner,
        renderer: RendererFn = _default_renderer,
        writer: WriterFn = _default_writer,
        summarizer: Optional[ProjectSummarizer] = None,
        cwd_fn: CwdFn = Path.cwd,
        output_filename: str = DEFAULT_OUTPUT_FILENAME,
    ) -> None:
        self._scanner = scanner
        self._renderer = renderer
        self._writer = writer
        self._summarizer = summarizer
        self._cwd_fn = cwd_fn
        self._output_filename = output_filename

    def execute(self, context: ReplContext) -> CommandResult:  # noqa: ARG002 - 컨텍스트 미사용
        root = self._cwd_fn().resolve()
        snapshot = self._scanner(root)

        llm_summary: Optional[str] = None
        if self._summarizer is not None:
            llm_summary = self._safely_summarize(snapshot)

        markdown = self._renderer(snapshot, llm_summary)
        output_path = (root / self._output_filename).resolve()
        result = self._writer(markdown, output_path)

        lines: list[str] = []
        if result.backup_path is not None:
            lines.append(f"Existing file backed up to: {result.backup_path}")
        if llm_summary:
            lines.append("Included LLM summary from Ollama.")
        lines.append(f"Saved project snapshot to: {result.output_path}")
        return CommandResult.cont("\n".join(lines))

    # ------------------------------------------------------------------ helpers

    def _safely_summarize(self, snapshot: ProjectSnapshot) -> Optional[str]:
        """Summarizer 호출에서 발생하는 모든 예외를 흡수한다.

        Ollama 서버가 꺼져 있어도 명령이 정상 완료되도록 광범위한 ``Exception`` 을
        잡는다. 시스템 예외(``KeyboardInterrupt``, ``SystemExit``)는 전파한다.
        """
        try:
            text = self._summarizer(snapshot)  # type: ignore[misc]
        except Exception:
            return None
        if not isinstance(text, str):
            return None
        text = text.strip()
        return text or None


__all__ = [
    "DEFAULT_BACKUP_SUFFIX",
    "DEFAULT_OUTPUT_FILENAME",
    "InitCommand",
    "ProjectSummarizer",
    "WriteResult",
    "write_snapshot_file",
]
