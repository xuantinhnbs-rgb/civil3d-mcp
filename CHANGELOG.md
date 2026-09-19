# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-09-19

First public release. The server was developed inside a larger working repository
alongside several other MCP servers; this is the point at which it became a
repository of its own.

### Added

- **58 MCP tools** covering surfaces, alignments, profiles, corridors, cross
  sections and data export, verified against Civil 3D 2026 (COM 13.8).
- `install.py` — detects this machine's Python interpreter and repository path,
  verifies dependencies, loads the server to confirm its tools register, reports
  which Civil 3D installation and COM version it found, and writes `.mcp.json`.
  `--check` verifies without writing; `--claude-desktop` also writes Claude
  Desktop's config. Civil 3D does not need to be running.
- Bilingual documentation: [README.md](README.md) (English) and
  [README.vi.md](README.vi.md) (Vietnamese), including the full table of 16
  measured differences between Civil 3D's COM API and its documentation.
- Proof images in [docs/images/](docs/images/), each with the exact call sequence
  that produced it recorded in `docs/images/README.md`.
- CI on Windows across Python 3.10–3.13, plus `ruff` on Linux. Civil 3D is **not**
  installed on the runner, which is deliberate: the offline suite has to pass
  without it, and the live suite has to skip itself cleanly.
- Community files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue
  and pull-request templates, Dependabot.
- `tests/test_tool_contracts.py` — derives the tool count from the source with an
  AST pass and requires the loaded server, both READMEs and that count to agree.
  It also checks that every `delete_*` tool takes a `confirm` parameter, and that
  the number of differences stated in prose matches the number of rows in the
  table.
- `tests/test_repo_layout.py` — checks what someone else receives on clone: a
  valid config example, `install.py` pointing at a script that exists, every
  documented image present and every present image documented, no machine-specific
  absolute paths in the public text, and a `.gitignore` that blocks drawings and
  survey data from a public repository.
- `tests/test_install.py` — regressions for the two `install.py` fixes below.

### Fixed

- **`install.py` died with a `SyntaxError` when the repository sat at a drive
  root.** The path was embedded into a generated snippet as `r'%s'`, so `X:\`
  produced `r'X:\'` — the trailing backslash swallowed the closing quote. Now via
  `%r`. The bug never appears for a path with a subdirectory, so it survived every
  test until the repository was mapped to a virtual drive for a screenshot.
- **`install.py` died with a `UnicodeEncodeError` on the default Windows cp1252
  console.** An accented character in a status line ended the diagnostic run partway
  through with a traceback about codecs — the tool that exists to diagnose problems,
  failing on the machines most likely to have them. Console output is now ASCII by
  convention, enforced by a test, with the print helper catching the error as a
  second line of defence.
- **`scripts/capture-window.ps1` could photograph the wrong window, in two ways,
  and the result was indistinguishable from a correct capture.** When several
  windows matched, it printed a warning and captured the first. And because the
  image is a copy of the screen region the window occupies, a `SetForegroundWindow`
  call that Windows refused — which is common — left it capturing whatever happened
  to be on top. Both are now errors; `-ProcessId` selects a window unambiguously,
  the foreground is verified immediately before the pixels are copied, and `-Force`
  restores the old behaviour deliberately.

### Documentation

- **Corrected two figures that had drifted from the code.** The README claimed 86
  offline tests where a real run gave 92, and claimed the live suite had found
  "eight" differences beside a table listing sixteen — two sentences seventy lines
  apart in the same file. Both are now covered by tests rather than by proofreading,
  and volatile counts have been removed from the prose entirely.
- Removed the absolute paths of the author's machine from the install instructions;
  `install.py` generates them now.
- Rewrote the opening, which described this server's role relative to sibling
  servers "already in the repo" — true while it was a subdirectory, false as a
  standalone repository. It now links to `recap-mcp` as the separate project it is.
- Documented that `create_new_drawing` needs a **full path** in `template_path`. A
  bare filename is silently ignored and you get Civil 3D's default template, whose
  only surface style is `Standard` — which displays nothing. The symptom is a
  drawing that looks empty, not an error.

[Unreleased]: https://github.com/xuantinhnbs-rgb/civil3d-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/xuantinhnbs-rgb/civil3d-mcp/releases/tag/v1.0.0
