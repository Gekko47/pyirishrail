# Project Conventions — `irish_rail`

Always-applied rules for this repository. These hold whether or not a skill is
loaded. If a rule here conflicts with a skill, the rule wins.

## Layout

| Path | Contents |
|---|---|
| `custom_components/irish_rail/` | the integration source |
| `custom_components/irish_rail/client.py`, `models.py`, `errors.py`, `request_gate.py`, `lib_const.py` | the framework-agnostic client — **no Home Assistant imports** |
| `tests/components/irish_rail/` | the test suite (mirrors the integration) |
| `tests/win_stubs.py` | Windows shim, see below |
| `scripts/` | CLI wrappers, e.g. `build_stops_matrix.py` |
| `docs/architecture.md` | long-form design invariants |
| `config/configuration.yaml` | local dev config |

The integration is loaded by HACS directly from the directory tree. It is
**not** pip-installed and there is no wheel to build.

## Toolchain

- **Python 3.14** (`pyproject.toml` `python_version = "3.14"`, CI
  `python-version: "3.14"`). Do not target an older Python than Home
  Assistant.
- **ruff** `>=0.16` — `ruff check custom_components/irish_rail
  tests/components/irish_rail scripts`
- **mypy strict** — `mypy custom_components/irish_rail
  tests/components/irish_rail`. `strict = true`,
  `explicit_package_bases = true`, `warn_unused_configs = true`. The
  integration **and its tests** are in the strict run.
- **pytest** at **100% line coverage** — the gate does not drop.

Run all three before proposing any change:

```bash
ruff check custom_components/irish_rail tests/components/irish_rail scripts
mypy custom_components/irish_rail tests/components/irish_rail
pytest tests/components/irish_rail tests/test_win_stubs.py \
  --cov=custom_components/irish_rail --cov-fail-under=100
```

## Dependencies

**Zero third-party runtime dependencies.** `manifest.json` `requirements: []`.

`aiohttp` comes from Home Assistant core — never list it as an integration
requirement. XML parsing is stdlib `xml.etree.ElementTree` with an explicit
pre-parse DTD/entity guard. The `defusedxml` question is closed; re-adding it
is a roadmap-level decision, not a local one.

`pyproject.toml` is **tooling-only**: `[tool.coverage.report]`,
`[tool.mypy]`, `[tool.pytest.ini_options]`. It does not build a wheel. Do not
add `setup.py`, `setup.cfg`, or `requirements.txt` without a demonstrated
compatibility reason.

## Never reintroduce

These legacy patterns were removed during the Bronze migration. Do not bring
them back:

- `requests`
- blocking I/O
- `xml.dom.minidom`
- untyped plain dictionaries
- swallowed exceptions
- old unittest-only architecture
- Travis CI
- `setup.py` / `requirements.txt` as the primary packaging model
- the `pyirishrail/` sub-package (Phase B1 deleted it)
- `gate.py` / `health.py` as separate modules (Phase B3 folded them into
  `_runtime.py`)
- a second or third stops-matrix rebuild implementation (B2 unified them in
  `matrix_rebuild.py`)
- publishing `pyirishrail` to PyPI — the name is owned by an unrelated
  project; decision S2 keeps the client internal
- `hass.data[DOMAIN][entry_id]` for runtime objects

## Patch targets

The client class lives directly in `client.py`. The valid path is:

```
custom_components.irish_rail.client.IrishRailClient.X
```

`patch("pyirishrail.…")` is obsolete — CI **fails the build** if it reappears
in `tests/components/irish_rail`.

## Windows test shim

`pyproject.toml` sets `addopts = "-p tests/win_stubs"`. The shim must load
**before** the entry-point plugins, because
`pytest-homeassistant-custom-component` imports `homeassistant.runner`, which on
Windows pulls in the POSIX-only `fcntl` module. The shim is a no-op elsewhere.

**Do not remove it.** Removing the shim breaks the suite on Windows.

## Docstrings and comments

- Contract docstrings: one or two lines.
- Non-obvious invariants: keep, with a `See docs/architecture.md §N` pointer
  if the rationale exceeds a sentence.
- Design history and narration: **not in source.** Move to
  `docs/architecture.md` or delete.
- **No `Skill N` / `Phase N` / `roadmap N.X` cross-references in source** — CI
  fails the build on these. Those breadcrumbs belong in the active roadmap
  file.
- Docstring density is gated in CI. Keep it within the active phase threshold.

## Never guess upstream semantics

If an Irish Rail XML field's meaning is uncertain — negative `Duein`,
cancellation markers, missing fields, date formats, direction values, station
code semantics, HTTPS availability — **inspect the original fixtures/tests and
actual API behaviour**. Do not silently guess.

When uncertain:
1. state the uncertainty,
2. implement defensive parsing,
3. add a test,
4. add a TODO if it cannot be resolved.

Never invent a service, trigger, or condition merely to make a checklist item
appear implemented. Document the exemption instead.

## Code style

Ruff is the authority for formatting and lint. Follow its output: 4-space
indent, double quotes, trailing commas in multi-line literals, sorted imports.
Do not hand-format against the tool — run `ruff check` and fix what it reports.
