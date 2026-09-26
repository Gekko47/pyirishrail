---
name: mcp-tooling
description: The MCP servers configured for this workspace and how each one is used in practice. Load when a task needs filesystem browsing, git history or diffs, library/API documentation lookup, or structured multi-step reasoning; when a question touches .roo/mcp.json, "which tools are available", "why isn't the git server working", or MCP setup, adding, removing, or repairing a server; or before choosing between a built-in read_file/list_files/search_files call and an MCP equivalent.
---

# MCP Tooling

Four MCP servers are configured in [`.roo/mcp.json`](../../mcp.json). This
skill records what each one actually provides, which are loaded, and the
constraints that apply when using them.

`.roo/mcp.json` is the **authority**. If this skill and the config file
disagree, the config file wins — verify by listing the tools actually exposed
in the session before relying on any claim here.

---

## 1. Server inventory

| Server | Package | Status | Use it for |
|---|---|---|---|
| `filesystem` | `@modelcontextprotocol/server-filesystem` | loaded | Directory trees, file metadata, batch reads, image inspection |
| `git` | `@modelcontextprotocol/inspector` + `uvx mcp-server-git` | **not loading** | Intended: status, log, diff, blame. Currently unavailable — see §2 |
| `context7` | `@upstash/context7-mcp` | loaded | Up-to-date library and framework documentation |
| `sequentialthinking` | `@modelcontextprotocol/server-sequential-thinking` | loaded | Structured, revisable multi-step reasoning |

---

## 2. Known defect — the `git` server does not start

The `git` entry is configured as:

```json
"git": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/inspector", "uvx", "mcp-server-git", ...]
}
```

That is the **MCP Inspector CLI invocation**, pasted into a server
definition. The inspector is a debugging proxy that hosts a web UI; it is not
an MCP server, so no `git` tools are ever exposed to the model.

**Do not assume git tools are available.** Use `execute_command` with `git`
until this is fixed.

**The fix**, when the user asks for it — replace the `git` entry with a direct
invocation of the real server:

```json
"git": {
  "command": "uvx",
  "args": ["mcp-server-git", "--repository", "C:/repos/pyirishrail"]
}
```

Requires `uv`/`uvx` on PATH. After editing, the Roo session must be restarted
before the new tools appear; MCP servers are connected at startup, not hot-
reloaded.

### Path argument hygiene

Both existing path args carry a doubled separator — `C://repos//pyirishrail`.
Windows tolerates it, but normalise to `C:/repos/pyirishrail` when touching
`.roo/mcp.json`. The `filesystem` server is rooted at the workspace only, so it
cannot read outside the repository.

---

## 3. `filesystem` — browsing the tree

The project already exposes first-class equivalents in the built-in tool set:

| Built-in (prefer) | MCP `filesystem` equivalent |
|---|---|
| `list_files` | `mcp__filesystem__list_directory` |
| `read_file` | `mcp__filesystem__read_text_file` |
| `search_files` | `mcp__filesystem__search_files` |
| — | `mcp__filesystem__get_file_info` (size, mtime) |
| — | `mcp__filesystem__read_multiple_files` (batch) |
| — | `mcp__filesystem__directory_tree` |
| — | `mcp__filesystem__edit_file` (line-based, returns a diff) |
| — | `mcp__filesystem__move_file` |
| — | `mcp__filesystem__create_directory` |
| — | `mcp__filesystem__read_media_file` (image/audio analysis) |

**Prefer the built-ins.** They are relative to the workspace root and need no
absolute path rewriting. Reach for the MCP server when the built-in has no
equivalent — file metadata, recursive JSON trees, batch reads across many
files, or inspecting an image.

`mcp__filesystem__edit_file` returns a git-style diff and supports a `dryRun`
preview. Use `apply_diff` for ordinary edits; reserve `edit_file` for
multi-hunk, exact-text-match replacements where the diff preview adds value.

---

## 4. `context7` — live library documentation

Two-step, in this order:

1. `mcp__context7__resolve-library-id` with the library name and a query
   describing what is needed.
2. `mcp--context7--query-docs` with the returned `/org/project` id and **one
   focused topic per call**.

Budget: at most 3 calls each per question. If the answer is not found in three
attempts, say so rather than continuing to search.

**Project rule:** this integration has **zero third-party runtime
dependencies** — `manifest.json` `requirements: []`. `aiohttp` comes from Home
Assistant core and is never listed as an integration requirement. Use
`context7` to read API documentation, never to justify adding a dependency.
Adding one requires roadmap sign-off (see
[ha-integration-conventions](../ha-integration-conventions/SKILL.md)).

---

## 5. `sequentialthinking` — structured reasoning

Use for problems that genuinely need decomposition: multi-file refactor
planning, debugging with several plausible root causes, or a design choice
with real trade-offs. Each step carries `thoughtNumber`, a total, and a
`nextThoughtNeeded` flag; steps may revise or branch from earlier ones.

Do **not** use it for a straightforward edit, a single-file lookup, or a
question with one correct answer. The overhead is not repaid, and the rules
in [`.roo/rules/`](../rules/) already encode the recurring decisions.

---

## 6. Constraints that apply to every server

- **Never reintroduce blocking I/O.** The "no blocking calls in an async path"
  rule in [`.roo/rules/ha-integration-rules.md`](../rules/ha-integration-rules.md)
  applies to the integration source. MCP tools are development-time only and
  never appear in shipped code.
- **MCP servers are not a dependency channel.** Nothing in
  `custom_components/irish_rail/` may import from, shell out to, or otherwise
  depend on an MCP server. The integration must build and test with every MCP
  server disconnected.
- **No project-internal cross-references in source.** Pointers like
  `mcp-tooling §2` belong in the roadmap, not in `.py` files. CI fails the
  build on this.
- **Session-scoped.** Servers connect at Roo startup. Editing `.roo/mcp.json`
  requires a restart before any change takes effect.
- **Secrets.** `context7` is configured with
  `env.DEFAULT_MINIMUM_TOKENS = ""`. Never put an API key, token, or
  credential into `.roo/mcp.json` — it is a tracked file in the repository.
