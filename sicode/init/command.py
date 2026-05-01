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

import os
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


class UnsafeOutputPathError(OSError):
    """``write_snapshot_file`` 이 보안 정책상 거부한 출력 경로.

    구체적으로 ``output_path`` 자체 또는 백업 대상 경로가 심볼릭 링크인 경우.
    심볼릭 링크를 따라가면 ``shutil.copy2`` / ``Path.write_text`` 가 외부 임의
    파일을 백업/덮어쓸 수 있어 보안 결함이 된다(데이터 유출/임의 파일 변조).
    """


@dataclass(frozen=True)
class WriteResult:
    """파일 저장 결과.

    Attributes:
        output_path: 저장된 마크다운 파일의 절대 경로.
        backup_path: 백업이 만들어졌으면 그 절대 경로, 없으면 ``None``.
    """

    output_path: Path
    backup_path: Optional[Path]


def _is_symlink(path: Path) -> bool:
    """``Path.is_symlink`` 의 ``OSError`` 안전 래퍼.

    ``lstat`` 호출이 권한 등으로 실패한 경우 보수적으로 ``True`` (심볼릭 가능성)
    로 간주해 호출자가 거부하도록 만든다. 정상 디렉토리에서도 권한 문제로
    실패할 수 있으므로, 호출자는 거부 시 사용자에게 "권한 또는 lstat 실패"
    가능성을 함께 안내해야 한다.
    """
    try:
        return path.is_symlink()
    except OSError:
        return True


def _validate_output_path(
    output_path: Path,
    backup_path: Optional[Path],
) -> None:
    """``write_snapshot_file`` 의 보안 정책 검사.

    ``output_path`` 자체 또는 백업 대상 경로가 심볼릭 링크면
    :class:`UnsafeOutputPathError` 를 던진다. 정책을 함수로 분리해 향후
    "디렉토리 경계 검사" 등 새 정책 추가 시 본 함수만 확장하면 되도록 한다(OCP).

    호출자(``InitCommand``)는 ``Path.absolute()`` (resolve 가 아님)로 만든
    경로를 넘겨야 한다. ``resolve()`` 는 심볼릭 링크를 따라가 본 검사를 무력화
    하므로(라운드 3 회귀), 호출 측에서 보장해야 한다.
    """
    if _is_symlink(output_path):
        raise UnsafeOutputPathError(
            f"refusing to write through a symlink "
            f"(or lstat denied): {output_path}"
        )
    # 백업 경로는 호출자가 "기존 파일이 존재할 때만" 결정한다. 존재할 때만
    # 심볼릭 검사. ``os.path.lexists`` 로 명시적으로 묶어 의도를 가시화한다.
    if backup_path is not None and os.path.lexists(str(backup_path)):
        if _is_symlink(backup_path):
            raise UnsafeOutputPathError(
                f"refusing to overwrite backup symlink "
                f"(or lstat denied): {backup_path}"
            )


def write_snapshot_file(
    markdown: str,
    output_path: Path,
    *,
    backup_suffix: str = DEFAULT_BACKUP_SUFFIX,
) -> WriteResult:
    """마크다운을 ``output_path`` 에 저장한다. 이미 파일이 있으면 백업한다.

    백업은 ``output_path`` 와 같은 디렉토리에서 같은 파일명에 ``backup_suffix`` 를
    덧붙인 경로로 생성된다(기존 백업이 있으면 덮어쓴다 — 단일 백업만 유지).

    보안 정책 (자세한 것은 :func:`_validate_output_path` 참조):
        ``output_path`` 또는 백업 대상 경로가 **심볼릭 링크**라면
        :class:`UnsafeOutputPathError` 를 던지고 어떤 쓰기도 수행하지 않는다.
        심볼릭 링크를 따라가면 ``/etc/passwd`` 같은 외부 파일이 백업으로
        복제되거나 덮어쓰일 수 있는 데이터 유출/임의 변조 경로가 열린다.
        백업은 :func:`os.replace` 로 수행해 원본 파일(또는 링크 메타데이터)을
        원자적으로 백업 경로로 이동하므로 link follow 가 발생하지 않는다.
        이후 :func:`os.open` 에 ``O_NOFOLLOW`` 플래그로 새 파일을 만들어 본문을
        쓰므로 출력 경로 작성 단계에서도 link follow 가 차단된다.

    호출자 계약 (Critical):
        호출자는 **반드시 ``Path.absolute()`` (또는 동등한 비-resolve 경로)** 를
        넘겨야 한다. ``Path.resolve()`` / ``os.path.realpath`` 는 심볼릭 링크를
        선제적으로 따라가므로 본 함수의 ``is_symlink`` 검사가 무력화된다
        (라운드 3 회귀). ``InitCommand.execute`` 가 이 계약을 지킨다.

    플랫폼:
        ``O_NOFOLLOW`` 는 Linux/macOS 에서 leaf 심볼릭을 거부한다. 일부 NFS /
        특수 파일시스템에서는 무시될 수 있어 단독 방어선으로 신뢰하지 말고,
        반드시 위 ``_validate_output_path`` 의 사전 검사와 결합한다.

    Args:
        markdown: 저장할 본문.
        output_path: 저장 경로(절대 권장, ``absolute()`` 결과 권장).
        backup_suffix: 백업 파일에 덧붙일 접미사. 기본값 ``".bak"``.

    Returns:
        :class:`WriteResult`.

    Raises:
        UnsafeOutputPathError: ``output_path`` 또는 백업 대상이 심볼릭 링크일 때.
    """
    # 1) 출력 경로 자체의 심볼릭 검사를 먼저 수행. 백업 경로는 출력이 이미 존재
    #    할 때만 의미가 있으므로, ``lexists`` 분기 안에서 함께 검증한다.
    backup_path: Optional[Path] = None
    if os.path.lexists(str(output_path)):
        backup_path = output_path.with_name(output_path.name + backup_suffix)

    _validate_output_path(output_path, backup_path)

    if backup_path is not None:
        # ``shutil.copy2`` 는 source 의 링크를 follow 하므로 사용하지 않는다.
        # ``os.replace`` 는 원본 파일을 (혹은 링크 자체를) 그대로 백업 경로로
        # 이동시키므로 데이터 유출 경로가 닫힌다. 또한 원자적이다.
        os.replace(str(output_path), str(backup_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ``Path.write_text`` 는 내부적으로 ``open`` 을 사용하며 결과적으로 링크를
    # follow 한다. 위 검사에서 링크는 거부했지만 TOCTOU 회피를 위해 ``O_NOFOLLOW``
    # 플래그를 명시한다. ``O_EXCL`` 까지 결합하면 백업 직후 race 로 새로 생긴
    # 링크/파일도 만들지 못해 보다 안전하다.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    if hasattr(os, "O_EXCL") and backup_path is not None:
        # 백업으로 옮겼으니 출력 경로는 비어 있어야 하며, 새로 만들어진다.
        flags |= os.O_EXCL
    try:
        fd = os.open(str(output_path), flags, 0o644)
    except OSError as exc:
        # ``O_NOFOLLOW`` 가 링크를 만났을 때 ``ELOOP`` 또는 플랫폼 별 에러로
        # 떨어진다. 사용자에게 보안 의도를 명확히 전달.
        raise UnsafeOutputPathError(
            f"refusing to write through a symlink (race or stale): {output_path}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(markdown)
    except Exception:
        # fdopen 실패 시 fd 누수 방지(정상 경로에서는 with 가 close).
        try:
            os.close(fd)
        except OSError:
            pass
        raise
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
        # 보안 주의(리뷰 라운드 3): ``Path.resolve()`` 는 심볼릭 링크를 따라가
        # 타겟의 실제 경로를 반환한다. 그 결과 SICODE.md 자리에 외부 파일을
        # 가리키는 심볼릭 링크가 있으면, writer 가 받은 경로는 외부 파일의
        # 실제 경로가 되어 ``write_snapshot_file`` 의 ``is_symlink`` 검사가
        # 무력화된다(타겟은 일반 파일이므로 통과). 따라서 cwd / output 모두
        # ``Path.absolute()`` 로 심볼릭을 풀지 않은 절대 경로만 만든다.
        root = self._cwd_fn().absolute()
        snapshot = self._scanner(root)

        llm_summary: Optional[str] = None
        if self._summarizer is not None:
            llm_summary = self._safely_summarize(snapshot)

        markdown = self._renderer(snapshot, llm_summary)
        output_path = (root / self._output_filename).absolute()
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
    "UnsafeOutputPathError",
    "WriteResult",
    "write_snapshot_file",
]
