"""Tests for the repository build gates under ``scripts/``.

These gates are part of CI, so they are covered and typed like the rest of
the repository rather than trusted as untested shell.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str) -> ModuleType:
    """Import a script by path; they are run as files, not as a package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hygiene() -> ModuleType:
    """The source-hygiene gate."""
    return _load("check_streamline_a4")


@pytest.fixture
def release() -> ModuleType:
    """The release-consistency gate."""
    return _load("check_release_version")


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession``; the CLI never uses it."""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.mark.parametrize("error", [None, "boom"])
def test_the_seed_cli_drives_the_sampler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: str | None
) -> None:
    """The offline seed CLI wires its flags straight into the sampler."""
    from custom_components.irish_rail.matrix_rebuild import RebuildResult

    cli = _load("build_stops_matrix")
    seen: dict[str, object] = {}

    async def _fake_sampler(
        client: object, **kwargs: object
    ) -> RebuildResult:
        seen.update(kwargs)
        return RebuildResult(sampled=3, error=error)

    # The real asyncio.run drives the CLI's own coroutine; only the HTTP
    # session and the sampler (which would hit the live API) are faked.
    monkeypatch.setattr(cli, "aiohttp", SimpleNamespace(ClientSession=_FakeSession))
    monkeypatch.setattr(cli, "IrishRailClient", lambda session: session)
    monkeypatch.setattr(cli, "sample_stops_matrix", _fake_sampler)
    output = tmp_path / "seed.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_stops_matrix.py", "--limit", "1", "--output", str(output)],
    )

    cli.main()

    assert seen == {
        "gap_fill": False,
        "atomic_dump": True,
        "priority": "normal",
        "delay": 0.3,
        "limit": 1,
        "output_path": output,
    }


def _source_tree(root: Path, body: str) -> Path:
    """Write a one-file integration tree under ``root`` and return its path."""
    package = root / "custom_components" / "irish_rail"
    package.mkdir(parents=True)
    (package / "clean.py").write_text(body, encoding="utf-8")
    return package


def _point(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, **values: Path
) -> Path:
    """Redirect a gate's module-level paths at a temporary tree."""
    for name, value in values.items():
        monkeypatch.setattr(module, name, value)
    return next(iter(values.values()))


def test_a_clean_tree_passes(
    hygiene: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One documented module, no breadcrumbs: nothing to report."""
    root = _source_tree(
        tmp_path,
        '"""Module."""\n\n'
        + "\n".join(f"VALUE_{n} = {n}" for n in range(20))
        + "\n\n\ndef helper() -> int:\n"
        '    """Return the first value."""\n    return VALUE_0\n',
    )
    _point(hygiene, monkeypatch, ROOT=root)
    assert hygiene.main() == 0


def test_an_empty_tree_does_not_divide_by_zero(
    hygiene: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No source at all is a density of zero, not a crash."""
    empty = tmp_path / "custom_components" / "irish_rail"
    empty.mkdir(parents=True)
    _point(hygiene, monkeypatch, ROOT=empty)
    assert hygiene.main() == 0


def test_docstring_budget_breach_is_reported(
    hygiene: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A module padded with docstring prose trips the density ceiling."""
    body = '"""Module."""\n\n\ndef helper() -> int:\n    """One."""\n    return 1\n'
    body += "\n\n" + "\n\n".join(
        f"def fn{n}() -> int:\n"
        '    """Line one.\n\n    Line two.\n\n    Line three.\n    """\n'
        f"    return {n}"
        for n in range(40)
    )
    root = _source_tree(tmp_path, body)
    _point(hygiene, monkeypatch, ROOT=root)
    assert hygiene.main() == 1


@pytest.mark.parametrize(
    "line",
    [
        "    # see Skill 10 for the rules",
        "    # see Phase A for the rules",
        "    # see the roadmap for the rules",
    ],
)
def test_project_internal_cross_references_are_rejected(
    hygiene: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, line: str
) -> None:
    """``Skill N`` / ``Phase N`` / ``roadmap`` breadcrumbs never reach source."""
    root = _source_tree(tmp_path, f'"""Module."""\n\n{line}\n')
    _point(hygiene, monkeypatch, ROOT=root)
    assert hygiene.main() == 1


def test_a_pointer_to_a_deleted_module_is_rejected(
    hygiene: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A backticked module name must name a file that still exists."""
    root = _source_tree(
        tmp_path,
        '"""Module.\n\n    See ``gone.py`` for the invariant.\n    """\n\nVALUE = 1\n',
    )
    _point(hygiene, monkeypatch, ROOT=root)
    assert hygiene.main() == 1


def _release_files(tmp_path: Path, version: str, released: str) -> tuple[Path, Path]:
    """Write a manifest/CHANGELOG pair declaring ``version`` / ``released``."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(f'{{"version": "{version}"}}', encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        f"# Changelog\n\n## [{released}] — 2026-09-06\n\nBody.\n", encoding="utf-8"
    )
    return manifest, changelog


def _redirect(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    """Point the release gate at a temporary manifest/CHANGELOG pair."""
    manifest, changelog = _release_files(tmp_path, "0.5.1", "0.5.1")
    _point(release, monkeypatch, MANIFEST=manifest, CHANGELOG=changelog)
    return manifest, changelog


def test_manifest_changelog_and_tag_agree(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The happy path: all three sources report the same version."""
    _redirect(release, monkeypatch, tmp_path)
    assert release.main(["check_release_version.py", "v0.5.1"]) == 0


def test_a_bare_tag_is_accepted(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tag without the conventional ``v`` prefix still has to line up."""
    _redirect(release, monkeypatch, tmp_path)
    assert release.main(["check_release_version.py", "0.5.1"]) == 0


def test_no_tag_still_checks_the_pair(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Away from a tag push the manifest/CHANGELOG pair is all that applies."""
    _redirect(release, monkeypatch, tmp_path)
    assert release.main(["check_release_version.py"]) == 0


def test_a_manifest_changelog_mismatch_is_reported(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The manifest version is what the user sees; it must be the released one."""
    manifest, changelog = _release_files(tmp_path, "0.5.0", "0.5.1")
    _point(release, monkeypatch, MANIFEST=manifest, CHANGELOG=changelog)
    assert release.main(["check_release_version.py"]) == 1


def test_a_tag_mismatch_is_reported(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tag must not ship a manifest version nobody tested."""
    _redirect(release, monkeypatch, tmp_path)
    assert release.main(["check_release_version.py", "v0.5.0"]) == 1


def test_unreadable_release_metadata_fails_closed(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing keys and missing headings are failures, never a silent pass."""
    manifest, changelog = _redirect(release, monkeypatch, tmp_path)
    manifest.write_text("{}", encoding="utf-8")
    assert release.main(["check_release_version.py"]) == 1

    manifest.write_text('{"version": "0.5.1"}', encoding="utf-8")
    changelog.write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")
    assert release.main(["check_release_version.py"]) == 1
