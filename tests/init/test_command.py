"""``sicode.init.command.InitCommand`` 단위 테스트.

검증 범위:
    - scanner / renderer 를 mock 으로 주입했을 때 파일 저장과 REPL 출력이 올바르다.
    - ``SICODE.md`` 가 이미 존재하면 ``SICODE.md.bak`` 을 만들고 그 사실을 출력한다.
    - Ollama summarizer 예외는 흡수되어 정적 분석 결과만으로 정상 완료된다.
    - REPL 통합: ``register_default_commands`` 후 ``/init`` 입력으로 명령이 실행된다.
"""

from __future__ import annotations

import os
import sys

import pytest

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
    UnsafeOutputPathError,
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="symlink 정책 테스트는 POSIX 환경 가정. Windows 는 권한 모델이 다르다.",
)
class TestWriteSnapshotFileSymlinkSafety:
    """``write_snapshot_file`` 의 심볼릭 링크 보안 회귀 테스트.

    PR #12 리뷰 라운드 2 [Critical]: SICODE.md 자리에 외부 파일을 가리키는
    심볼릭 링크가 있을 때 ``shutil.copy2`` + ``Path.write_text`` 가 링크를
    그대로 따라가 (1) 외부 파일 내용이 ``SICODE.md.bak`` 로 유출되고
    (2) 외부 파일이 마크다운으로 덮어쓰이는 결함이 있었다. 본 테스트는
    이 회귀를 영구적으로 막는다.
    """

    def test_refuses_when_target_is_symlink_to_external_file(
        self, tmp_path: Path
    ) -> None:
        # 외부 "민감" 파일 - 절대로 백업/덮어쓰기되어선 안 됨.
        external = tmp_path / "external_secret.txt"
        external.write_text("SECRET CONTENT", encoding="utf-8")

        # SICODE.md 자리에 외부 파일을 가리키는 심볼릭 링크 배치.
        target = tmp_path / "SICODE.md"
        os.symlink(str(external), str(target))
        assert target.is_symlink()

        with pytest.raises(UnsafeOutputPathError):
            write_snapshot_file("new content", target)

        # 외부 파일 내용은 그대로 보존되어야 한다(데이터 유출/변조 방지).
        assert external.read_text(encoding="utf-8") == "SECRET CONTENT"
        # 백업 파일이 만들어져선 안 된다.
        backup = tmp_path / ("SICODE.md" + DEFAULT_BACKUP_SUFFIX)
        assert not backup.exists()
        # 링크 자체도 그대로 유지(우리가 건드리지 않음).
        assert target.is_symlink()

    def test_refuses_when_target_is_symlink_even_to_nonexistent(
        self, tmp_path: Path
    ) -> None:
        # 깨진 심볼릭 링크여도 정책상 거부.
        target = tmp_path / "SICODE.md"
        os.symlink(str(tmp_path / "does_not_exist"), str(target))

        with pytest.raises(UnsafeOutputPathError):
            write_snapshot_file("data", target)

        # 백업 / 실제 파일 모두 만들어져선 안 됨.
        backup = tmp_path / ("SICODE.md" + DEFAULT_BACKUP_SUFFIX)
        assert not backup.exists()
        # 링크는 그대로.
        assert target.is_symlink()

    def test_refuses_when_backup_path_is_symlink(self, tmp_path: Path) -> None:
        # SICODE.md 는 일반 파일, SICODE.md.bak 자리가 외부를 가리키는 심볼릭 링크.
        # 이 경우 백업이 외부 파일을 덮어쓰는 식으로 변조하면 안 된다.
        external = tmp_path / "external.txt"
        external.write_text("EXTERNAL", encoding="utf-8")

        target = tmp_path / "SICODE.md"
        target.write_text("OLD", encoding="utf-8")
        backup = tmp_path / ("SICODE.md" + DEFAULT_BACKUP_SUFFIX)
        os.symlink(str(external), str(backup))
        assert backup.is_symlink()

        with pytest.raises(UnsafeOutputPathError):
            write_snapshot_file("NEW", target)

        # 외부 파일은 변조되지 않아야 함.
        assert external.read_text(encoding="utf-8") == "EXTERNAL"
        # 원본 파일도 변하지 않아야 함(거부 후엔 어떤 쓰기도 일어나지 않는다).
        assert target.read_text(encoding="utf-8") == "OLD"
        # 백업 링크는 그대로.
        assert backup.is_symlink()

    def test_normal_flow_still_works_alongside_unrelated_symlinks(
        self, tmp_path: Path
    ) -> None:
        # 같은 디렉토리에 무관한 심볼릭 링크가 있어도 정상 흐름은 영향받지 않음.
        unrelated_target = tmp_path / "unrelated.txt"
        unrelated_target.write_text("unrelated", encoding="utf-8")
        os.symlink(str(unrelated_target), str(tmp_path / "unrelated.lnk"))

        target = tmp_path / "SICODE.md"
        target.write_text("OLD", encoding="utf-8")
        result = write_snapshot_file("NEW", target)
        assert target.read_text(encoding="utf-8") == "NEW"
        backup = tmp_path / ("SICODE.md" + DEFAULT_BACKUP_SUFFIX)
        assert backup.exists() and not backup.is_symlink()
        assert backup.read_text(encoding="utf-8") == "OLD"
        assert result.backup_path == backup
        # 무관한 외부 파일은 그대로.
        assert unrelated_target.read_text(encoding="utf-8") == "unrelated"


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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="symlink 정책 테스트는 POSIX 환경 가정. Windows 는 권한 모델이 다르다.",
)
class TestInitCommandSymlinkSafetyIntegration:
    """``InitCommand.execute`` end-to-end 보안 회귀 테스트 (라운드 3).

    리뷰 라운드 2 [Critical]:
        ``InitCommand.execute`` 가 ``output_path = (root / filename).resolve()`` 로
        심볼릭 링크를 미리 풀어 writer 에 넘기던 시기가 있었다. 그 결과
        ``write_snapshot_file`` 의 ``is_symlink`` 단위 보안은 통과해도,
        실제 통합 호출 경로에서는 외부 파일이 백업/덮어쓰기 당하는 데이터
        유출/임의 변조 결함이 재현됐다. 본 테스트는 이 통합 경로 회귀를 막는다.
    """

    def test_execute_refuses_when_sicode_md_is_symlink_to_external_file(
        self, tmp_path: Path
    ) -> None:
        # 외부 디렉토리에 "민감" 파일.
        external_dir = tmp_path / "external_dir"
        external_dir.mkdir()
        external = external_dir / "external_secret.txt"
        external.write_text("SECRET CONTENT", encoding="utf-8")

        # cwd 는 별도 디렉토리. 그 안의 SICODE.md 가 외부 파일을 가리키는 symlink.
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        target = cwd / "SICODE.md"
        os.symlink(str(external), str(target))
        assert target.is_symlink()

        cmd = InitCommand(cwd_fn=lambda: cwd)

        # InitCommand 가 resolve() 로 심볼릭을 풀면 writer 의 검사가 우회되어
        # CommandResult 가 정상 반환되는 결함이 있었다. 이제는 반드시 거부.
        with pytest.raises(UnsafeOutputPathError):
            cmd.execute(ReplContext())

        # 가장 중요한 사후 조건: 외부 파일은 어떤 식으로도 변조되거나 백업되지 않음.
        assert external.read_text(encoding="utf-8") == "SECRET CONTENT"
        # 외부 디렉토리에 백업 파일이 생성되어선 절대 안 된다(데이터 유출 경로 차단).
        assert not (external_dir / "external_secret.txt.bak").exists()
        # cwd 디렉토리에도 SICODE.md.bak 가 생기면 안 된다.
        assert not (cwd / "SICODE.md.bak").exists()
        # 심볼릭 링크 자체는 우리가 손대지 않으므로 그대로 유지.
        assert target.is_symlink()

    def test_execute_refuses_when_sicode_md_is_broken_symlink(
        self, tmp_path: Path
    ) -> None:
        # 깨진 심볼릭 링크여도 InitCommand 통합 경로에서 거부되어야 한다.
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        target = cwd / "SICODE.md"
        os.symlink(str(tmp_path / "does_not_exist"), str(target))

        cmd = InitCommand(cwd_fn=lambda: cwd)
        with pytest.raises(UnsafeOutputPathError):
            cmd.execute(ReplContext())

        # 백업 / 실제 파일 어느 쪽도 만들어져선 안 됨.
        assert not (cwd / "SICODE.md.bak").exists()
        # 링크 자체는 그대로.
        assert target.is_symlink()

    def test_execute_normal_when_cwd_parent_is_symlink_dir(
        self, tmp_path: Path
    ) -> None:
        # cwd 부모 디렉토리가 심볼릭 링크여도 (e.g. macOS 의 /tmp -> /private/tmp)
        # leaf SICODE.md 가 실제 파일이라면 정상 동작해야 한다.
        # ``Path.absolute()`` 는 심볼릭을 풀지 않으므로 leaf 만 체크된다.
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        os.symlink(str(real_dir), str(link_dir))

        # symlink 디렉토리를 cwd 로 사용.
        cmd = InitCommand(cwd_fn=lambda: link_dir)
        result = cmd.execute(ReplContext())
        # 정상 완료. 실제 파일이 link_dir(혹은 real_dir) 안에 생긴다.
        assert "Saved project snapshot to:" in result.output
        assert (link_dir / "SICODE.md").exists()
        assert (real_dir / "SICODE.md").exists()
