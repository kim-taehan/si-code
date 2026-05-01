"""``sicode.init.command.InitCommand`` 단위 테스트.

검증 범위:
    - scanner / renderer 를 mock 으로 주입했을 때 파일 저장과 REPL 출력이 올바르다.
    - ``SICODE.md`` 가 이미 존재하면 ``SICODE.md.bak`` 을 만들고 그 사실을 출력한다.
    - Ollama summarizer 예외는 흡수되어 정적 분석 결과만으로 정상 완료된다.
    - REPL 통합: ``register_default_commands`` 후 ``/init`` 입력으로 명령이 실행된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from sicode.commands import (
    register_default_commands,
)
from sicode.commands.base import CommandAction, ReplContext
from sicode.commands.registry import SlashCommandRegistry, dispatch_command
from sicode.init.command import (
    DEFAULT_BACKUP_SUFFIX,
    DEFAULT_OUTPUT_FILENAME,
    InitCommand,
    WriteResult,
    write_snapshot_file,
)
from sicode.init.scanner import (
    DirectoryEntry,
    ProjectSnapshot,
)
from sicode.repl import run_repl_with_inputs
from tests.conftest import EchoMode


def _empty_snapshot(root: Path) -> ProjectSnapshot:
    return ProjectSnapshot(
        root=str(root),
        tree=DirectoryEntry(path=".", depth=0),
        metadata_files=(),
        max_depth=4,
        max_file_bytes=1024 * 1024,
    )


class TestWriteSnapshotFile:
    def test_writes_when_no_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "SICODE.md"
        result = write_snapshot_file("hello", target)
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello"
        assert result.output_path == target
        assert result.backup_path is None

    def test_creates_backup_when_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "SICODE.md"
        target.write_text("old", encoding="utf-8")
        result = write_snapshot_file("new", target)
        assert target.read_text(encoding="utf-8") == "new"
        backup = tmp_path / ("SICODE.md" + DEFAULT_BACKUP_SUFFIX)
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "old"
        assert result.backup_path == backup


class TestInitCommandUnit:
    def test_uses_injected_scanner_and_renderer(self, tmp_path: Path) -> None:
        captured_scanned: list[Path] = []
        captured_render_args: list[tuple[ProjectSnapshot, Optional[str]]] = []
        write_calls: list[tuple[str, Path]] = []

        def fake_scanner(root: Path) -> ProjectSnapshot:
            captured_scanned.append(root)
            return _empty_snapshot(root)

        def fake_renderer(snap: ProjectSnapshot, summary: Optional[str]) -> str:
            captured_render_args.append((snap, summary))
            return "RENDERED"

        def fake_writer(markdown: str, path: Path) -> WriteResult:
            write_calls.append((markdown, path))
            return WriteResult(output_path=path, backup_path=None)

        cmd = InitCommand(
            scanner=fake_scanner,
            renderer=fake_renderer,
            writer=fake_writer,
            cwd_fn=lambda: tmp_path,
        )
        result = cmd.execute(ReplContext())
        assert result.action is CommandAction.CONTINUE
        assert captured_scanned == [tmp_path.resolve()]
        assert captured_render_args[0][1] is None  # no summary
        assert write_calls[0][0] == "RENDERED"
        assert write_calls[0][1] == (tmp_path / DEFAULT_OUTPUT_FILENAME).resolve()
        assert "Saved project snapshot to:" in result.output
        assert str((tmp_path / DEFAULT_OUTPUT_FILENAME).resolve()) in result.output

    def test_reports_backup_in_output(self, tmp_path: Path) -> None:
        backup = tmp_path / "SICODE.md.bak"

        def fake_writer(markdown: str, path: Path) -> WriteResult:
            return WriteResult(output_path=path, backup_path=backup)

        cmd = InitCommand(
            scanner=lambda r: _empty_snapshot(r),
            renderer=lambda s, l: "x",
            writer=fake_writer,
            cwd_fn=lambda: tmp_path,
        )
        result = cmd.execute(ReplContext())
        assert "Existing file backed up to:" in result.output
        assert str(backup) in result.output

    def test_summarizer_exception_is_swallowed(self, tmp_path: Path) -> None:
        renders: list[Optional[str]] = []

        def boom(snap: ProjectSnapshot) -> str:
            raise RuntimeError("ollama down")

        def fake_renderer(snap: ProjectSnapshot, summary: Optional[str]) -> str:
            renders.append(summary)
            return "OK"

        cmd = InitCommand(
            scanner=lambda r: _empty_snapshot(r),
            renderer=fake_renderer,
            writer=lambda md, p: WriteResult(output_path=p, backup_path=None),
            summarizer=boom,
            cwd_fn=lambda: tmp_path,
        )
        result = cmd.execute(ReplContext())
        assert result.action is CommandAction.CONTINUE
        # 요약 실패는 출력에 노출되지 않고, 렌더러에 None 이 전달된다.
        assert renders == [None]
        assert "Saved project snapshot to:" in result.output
        assert "LLM summary" not in result.output

    def test_summarizer_success_includes_in_render_and_message(
        self, tmp_path: Path
    ) -> None:
        renders: list[Optional[str]] = []

        def good(snap: ProjectSnapshot) -> str:
            return "Project does X."

        def fake_renderer(snap: ProjectSnapshot, summary: Optional[str]) -> str:
            renders.append(summary)
            return "OK"

        cmd = InitCommand(
            scanner=lambda r: _empty_snapshot(r),
            renderer=fake_renderer,
            writer=lambda md, p: WriteResult(output_path=p, backup_path=None),
            summarizer=good,
            cwd_fn=lambda: tmp_path,
        )
        result = cmd.execute(ReplContext())
        assert renders == ["Project does X."]
        assert "Included LLM summary" in result.output

    def test_summarizer_returns_blank_is_treated_as_none(self, tmp_path: Path) -> None:
        renders: list[Optional[str]] = []

        cmd = InitCommand(
            scanner=lambda r: _empty_snapshot(r),
            renderer=lambda s, l: (renders.append(l) or "OK"),
            writer=lambda md, p: WriteResult(output_path=p, backup_path=None),
            summarizer=lambda s: "   \n   ",
            cwd_fn=lambda: tmp_path,
        )
        result = cmd.execute(ReplContext())
        assert renders == [None]
        assert "LLM summary" not in result.output


class TestInitCommandIntegration:
    def test_end_to_end_writes_real_file(self, tmp_path: Path) -> None:
        # 실제 scanner/renderer/writer 사용. tmp 디렉토리에 파일 몇 개를 만들어둔다.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n', encoding="utf-8"
        )
        (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")

        cmd = InitCommand(cwd_fn=lambda: tmp_path)
        result = cmd.execute(ReplContext())
        out_path = (tmp_path / "SICODE.md").resolve()
        assert out_path.exists()
        assert "## Directory Tree" in out_path.read_text(encoding="utf-8")
        assert str(out_path) in result.output

    def test_existing_file_backup_flow(self, tmp_path: Path) -> None:
        target = tmp_path / "SICODE.md"
        target.write_text("OLD CONTENT", encoding="utf-8")

        cmd = InitCommand(cwd_fn=lambda: tmp_path)
        result = cmd.execute(ReplContext())

        backup = tmp_path / "SICODE.md.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "OLD CONTENT"
        assert "Existing file backed up to:" in result.output
        assert str(backup) in result.output

    def test_repl_dispatch_via_slash_init(self, tmp_path: Path) -> None:
        # 별도 레지스트리를 만들어 InitCommand 만 등록하고 dispatch_command 로 검증.
        reg = SlashCommandRegistry()
        cmd = InitCommand(cwd_fn=lambda: tmp_path)
        reg.register(cmd)
        result = dispatch_command("/init", registry=reg)
        assert result.action is CommandAction.CONTINUE
        assert (tmp_path / "SICODE.md").exists()
        assert "Saved project snapshot to:" in result.output

    def test_register_default_commands_includes_init(self) -> None:
        reg = SlashCommandRegistry()
        register_default_commands(registry=reg)
        names = [c.name for c in reg.commands()]
        assert "init" in names

    def test_repl_run_with_slash_init_does_not_call_mode_handle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Path.cwd 를 tmp_path 로 고정시키고, 기본 등록 절차로 명령을 실행.
        monkeypatch.chdir(tmp_path)
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/init", "/exit"])
        assert mode.calls == []
        joined = "\n".join(outputs)
        assert "Saved project snapshot to:" in joined
        assert (tmp_path / "SICODE.md").exists()
