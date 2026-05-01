"""``sicode.init.scanner`` 단위 테스트.

검증 범위:
    - 깊이 한도 (``max_depth``): 4단계 초과는 트리에 포함되지 않는다.
    - 무시 패턴 (``.git``, ``__pycache__``, ``.env``, ``*.pem``, ``id_rsa*``).
    - 1 MB 초과 파일: 본문 미수집, 메타데이터(``is_oversize=True``) 만 기록.
    - 바이너리 파일: NULL 바이트 포함 시 ``is_binary=True``.
    - 메타데이터 파일(``pyproject.toml`` 등) 본문 수집.
"""

from __future__ import annotations

from pathlib import Path

from sicode.init.scanner import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILE_BYTES,
    DirectoryEntry,
    FileEntry,
    ProjectSnapshot,
    scan_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_paths(node: DirectoryEntry) -> "list[str]":
    """트리에 포함된 모든 디렉토리/파일의 상대 경로(루트 ``.`` 제외)."""
    out: list[str] = []
    for d in node.directories:
        out.append(d.path)
        out.extend(_all_paths(d))
    for f in node.files:
        out.append(f.path)
    return out


def _find_file(node: DirectoryEntry, rel_path: str) -> "FileEntry | None":
    if node.path == rel_path:  # pragma: no cover - 루트 자체는 파일이 아님
        return None
    for f in node.files:
        if f.path == rel_path:
            return f
    for d in node.directories:
        found = _find_file(d, rel_path)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScannerDepthLimit:
    def test_excludes_files_beyond_max_depth(self, tmp_path: Path) -> None:
        # 트리: tmp_path(=루트, depth=0)/d1/d2/d3/d4/d5/d6/leaf.txt
        # ``range(1, 7)`` 으로 d1..d6 까지 6단계가 만들어진다(d6.depth=6).
        # leaf.txt 는 d6 안에 있으므로 depth=7. (이전 주석에 "leaf=6" 으로 적혀
        # 있었으나 잘못된 계산이었다 — 정확히는 depth=7 이다.)
        deep = tmp_path
        for i in range(1, 7):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        leaf = deep / "leaf.txt"
        leaf.write_text("hello", encoding="utf-8")

        # max_depth=4 이면 d4(depth=4) 까지 노출, d5(depth=5) 는 truncated sentinel
        # 로만 등장하고 d6/leaf.txt(depth>=6) 은 트리에 일절 포함되지 않는다.
        snap = scan_project(tmp_path, max_depth=4)
        all_paths = _all_paths(snap.tree)
        # leaf 의 전체 경로는 절대 포함되어선 안 된다(깊이 6이라 4단계 초과).
        assert all(not p.endswith("leaf.txt") for p in all_paths)
        # d4 까지는 트리에 등장한다.
        assert any(p.endswith("d4") for p in all_paths)

    def test_truncated_marker_on_depth_boundary(self, tmp_path: Path) -> None:
        # depth=1 까지만 허용하면 루트의 자식 디렉토리가 truncated=True 로 노출된다.
        (tmp_path / "child").mkdir()
        (tmp_path / "child" / "inner").mkdir()
        snap = scan_project(tmp_path, max_depth=1)
        # 루트 -> child(depth=1) 까지 보임. inner(depth=2) 는 노출되지 않는다.
        child = next(d for d in snap.tree.directories if d.path == "child")
        assert child.depth == 1
        # inner(depth=2)는 max_depth=1 이므로 child 의 children 도 채워지지 않는다.
        # _scan_directory 는 depth>max_depth 일 때만 truncated=True 를 set 한다.
        # depth==max_depth(=1) 이면 자식까지는 보지만 그 자식의 자식은 깊이 2가 되어
        # 다음 재귀에서 depth>max_depth 가 되어 truncated=True 로 등록된다.
        inner = next(d for d in child.directories if d.path == "child/inner")
        assert inner.truncated is True
        assert inner.directories == ()
        assert inner.files == ()


class TestScannerIgnorePatterns:
    def test_skips_dot_git_and_pycache(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "x.cpython-39.pyc").write_bytes(b"\x00\x01")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")

        snap = scan_project(tmp_path)
        all_paths = _all_paths(snap.tree)
        assert ".git" not in all_paths
        assert "__pycache__" not in all_paths
        # 정상 파일은 포함되어야 함
        assert "src/main.py" in all_paths

    def test_skips_secrets_env_and_pem_and_id_rsa(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
        (tmp_path / "server.pem").write_text("-----BEGIN-----", encoding="utf-8")
        (tmp_path / "id_rsa").write_text("priv", encoding="utf-8")
        (tmp_path / "id_rsa.pub").write_text("pub", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("ok", encoding="utf-8")

        snap = scan_project(tmp_path)
        names = {p.split("/")[-1] for p in _all_paths(snap.tree)}
        assert ".env" not in names
        assert "server.pem" not in names
        assert "id_rsa" not in names
        assert "id_rsa.pub" not in names
        assert "keep.txt" in names

    def test_ignored_files_do_not_appear_in_metadata(self, tmp_path: Path) -> None:
        # 보안 파일이 우연히 메타데이터 패턴과 겹쳐도 포함되어선 안 됨(.env 와 .toml 등은
        # 이름이 다르니, 여기서는 ``.env`` 만 빠지는지 확인).
        (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
        snap = scan_project(tmp_path)
        for meta in snap.metadata_files:
            assert ".env" not in meta.path

    def test_skips_extended_secret_patterns(self, tmp_path: Path) -> None:
        """리뷰 라운드 2: ``*.key`` / ``*.pfx`` / ``id_dsa`` / ``id_ed25519`` /
        ``credentials*`` / ``secrets.*`` / ``*.netrc`` / ``.npmrc`` / ``.pypirc``
        / ``id_rsa_legacy`` (와일드카드 변형) 까지 제외되는지 보강 검증.
        """
        names_to_create = [
            "client.key",
            "store.pfx",
            "android.jks",
            "java.keystore",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
            "id_rsa_legacy",  # 와일드카드 변형: id_rsa* 로 잡혀야 함
            "credentials",
            "credentials.json",
            "secrets.yaml",
            "secret.toml",
            ".npmrc",
            ".pypirc",
            ".netrc",
            "my.netrc",
        ]
        for name in names_to_create:
            (tmp_path / name).write_text("S", encoding="utf-8")
        # 정상 파일도 하나 두어 스캐너 자체는 동작해야 함을 확인.
        (tmp_path / "main.py").write_text("print('hi')", encoding="utf-8")

        snap = scan_project(tmp_path)
        present = {p.split("/")[-1] for p in _all_paths(snap.tree)}
        for forbidden in names_to_create:
            assert forbidden not in present, f"{forbidden} 이(가) 노출됨"
        assert "main.py" in present


class TestScannerOversize:
    def test_oversize_file_records_metadata_only(self, tmp_path: Path) -> None:
        big = tmp_path / "huge.txt"
        # max_file_bytes=10 으로 임계값을 낮춰 검증
        big.write_text("a" * 100, encoding="utf-8")
        snap = scan_project(tmp_path, max_file_bytes=10)
        entry = _find_file(snap.tree, "huge.txt")
        assert entry is not None
        assert entry.is_oversize is True
        assert entry.size_bytes == 100
        # 큰 파일은 메타데이터 본문 수집 대상이 아니다 (확장자가 .txt 라도)
        assert all(meta.path != "huge.txt" for meta in snap.metadata_files)

    def test_oversize_default_threshold_is_one_megabyte(self) -> None:
        assert DEFAULT_MAX_FILE_BYTES == 1024 * 1024


class TestScannerBinary:
    def test_binary_file_marked_and_not_collected(self, tmp_path: Path) -> None:
        bin_path = tmp_path / "data.bin"
        bin_path.write_bytes(b"abc\x00\x01\x02more")
        snap = scan_project(tmp_path)
        entry = _find_file(snap.tree, "data.bin")
        assert entry is not None
        assert entry.is_binary is True
        assert entry.is_oversize is False

    def test_text_file_not_marked_binary(self, tmp_path: Path) -> None:
        text = tmp_path / "note.txt"
        text.write_text("hello world", encoding="utf-8")
        snap = scan_project(tmp_path)
        entry = _find_file(snap.tree, "note.txt")
        assert entry is not None
        assert entry.is_binary is False


class TestScannerMetadataCollection:
    def test_collects_pyproject_and_readme(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n', encoding="utf-8"
        )
        (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("print('hi')", encoding="utf-8")

        snap = scan_project(tmp_path)
        meta_paths = {m.path for m in snap.metadata_files}
        assert "pyproject.toml" in meta_paths
        assert "README.md" in meta_paths
        # main.py 는 메타데이터 패턴에 매칭되지 않음
        assert "main.py" not in meta_paths

        py = next(m for m in snap.metadata_files if m.path == "pyproject.toml")
        assert "demo" in py.content
        assert py.truncated is False

    def test_metadata_truncates_oversize_text(self, tmp_path: Path) -> None:
        # 메타데이터 한도 미만이라도 절대 한도(max_file_bytes) 넘으면 메타데이터 미수집.
        # 여기서는 metadata_max_bytes 만 작게 잡고 max_file_bytes 는 충분히 크게 둔다.
        long_text = "x" * 5000
        (tmp_path / "README.md").write_text(long_text, encoding="utf-8")
        snap = scan_project(tmp_path, metadata_max_bytes=100)
        readme = next(m for m in snap.metadata_files if m.path == "README.md")
        assert readme.truncated is True
        assert len(readme.content) <= 100


class TestSnapshotShape:
    def test_snapshot_has_root_and_defaults(self, tmp_path: Path) -> None:
        snap = scan_project(tmp_path)
        assert isinstance(snap, ProjectSnapshot)
        assert snap.root == str(tmp_path.resolve())
        assert snap.max_depth == DEFAULT_MAX_DEPTH
        assert snap.max_file_bytes == DEFAULT_MAX_FILE_BYTES
        assert snap.tree.path == "."
        assert snap.tree.depth == 0
