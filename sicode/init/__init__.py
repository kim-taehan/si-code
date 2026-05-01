"""``/init`` 슬래시 명령 패키지.

현재 작업 디렉토리(:func:`pathlib.Path.cwd`)를 정적 분석해
``SICODE.md`` 마크다운 파일로 저장하는 기능을 제공한다.

설계 메모:
    - SRP: 디렉토리 스캔(:mod:`.scanner`), 마크다운 변환(:mod:`.renderer`),
      슬래시 명령 처리/파일 저장(:mod:`.command`) 책임을 모듈로 분리한다.
    - DIP: ``InitCommand`` 는 콜러블 인터페이스에 의존하며 scanner/renderer/
      summarizer 를 모두 주입 가능하다.
    - OCP: ``register_default_commands()`` 에 ``InitCommand()`` 한 줄을 더하는 것
      외에 REPL 코드는 변경하지 않는다.
"""

from __future__ import annotations

from sicode.init.command import InitCommand, write_snapshot_file
from sicode.init.renderer import render_markdown
from sicode.init.scanner import (
    DEFAULT_IGNORE_PATTERNS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_METADATA_PATTERNS,
    DirectoryEntry,
    FileEntry,
    MetadataFile,
    ProjectSnapshot,
    scan_project,
)

__all__ = [
    "DEFAULT_IGNORE_PATTERNS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_METADATA_PATTERNS",
    "DirectoryEntry",
    "FileEntry",
    "InitCommand",
    "MetadataFile",
    "ProjectSnapshot",
    "render_markdown",
    "scan_project",
    "write_snapshot_file",
]
