# Contributing

Thanks for taking an interest in this project. Issues and pull requests are
welcome — **in English or Vietnamese**, whichever you are comfortable with.

---

## Before you start

The server drives Civil 3D over COM, so meaningful development needs Windows. You
do **not** need Civil 3D installed to work on most of it: the offline suite uses a
fake COM layer, and `tests/test_repo_layout.py` runs anywhere.

Before proposing a new tool, check the **Known limits** section of the README.
Several obvious requests are blocked by Civil 3D's COM API itself — assemblies
cannot be created from scratch, corridor targets cannot be assigned, subassembly
parameters cannot be edited. Those were established against the type library, not
guessed, and a pull request cannot work around them.

---

## Setting up

```powershell
git clone https://github.com/xuantinhnbs-rgb/civil3d-mcp.git
cd civil3d-mcp

pip install -r requirements.txt
pip install -r requirements-dev.txt

python install.py --check     # server loads, tool count and Civil 3D version printed
```

---

## Before opening a pull request

Run the same three checks CI runs:

```powershell
ruff check .                  # lint (config in pyproject.toml)
pytest                        # offline suite
python install.py --check     # loads the server, counts its tools
```

All three must pass. If you have Civil 3D and changed anything that touches it,
also run the live suite:

```powershell
$env:CIVIL3D_LIVE_TEST=1; pytest tests/test_live_civil3d.py
```

---

## Code conventions

- **Comments and docstrings are written in Vietnamese.** Keep it that way in files
  that already use it.
- **Anything printed to a console stays ASCII.** `install.py` says why in a
  comment: the default Windows console is cp1252, and one accented character in a
  status line kills the diagnostic script at the moment someone needs it most.
  Docstrings and comments are unaffected — they never reach `print`.
- **Every COM call goes through `Civil3DClient.run()`.** COM interface pointers
  cannot cross threads, and the MCP server runs tools in a thread pool. Calling a
  module function directly from another thread does not fail at that line; it makes
  an unrelated line fail later, which is much harder to trace.
- **Every tool returns a dict with an `ok` key** and is wrapped so an exception
  becomes `{"ok": false, "error": "..."}` rather than a COM stack trace.
- **A tool's docstring is its description in the MCP schema.** It is what the model
  reads to decide whether to call the tool, so write it for that reader.
- `ruff` settings live in `pyproject.toml`. `UP` (pyupgrade) is deliberately off —
  tool signatures use `Optional[...]` / `Dict[...]`, and those annotations generate
  the JSON schema the model sees.

---

## Adding a tool — checklist

1. Put the work in the right module (`surfaces.py`, `alignments.py`,
   `corridors.py`, `research.py`) and keep the wrapper in `server.py` thin.
2. Route it through `Civil3DClient.run()`.
3. **Verify the write by reading it back**, and return `verified` and
   `verified_by` saying how. A COM call that does not raise is not evidence that
   anything happened — return the count you *measured*, never the count you sent.
   Beware of reading back too early: `Alignment.Entities.Count` is still 0 right
   after adding a segment, and `Corridors.Add` does not appear in the collection on
   the first read. A verification that runs too early reports a successful
   operation as broken, and the user then creates a second one.
4. **Add it to the tool table in both `README.md` and `README.vi.md`.**
   `test_tool_contracts.py` fails if either is missing it, and the tool-count
   heading is checked against a count parsed from the source, so the numbers
   cannot drift.
5. If the tool deletes anything, give it a `confirm` parameter — a test enforces
   this for every `delete_*` tool.
6. Add a test whose expected value you can compute by hand.

---

## Testing conventions

**Every fixture has an analytic answer.** Not "the output looks reasonable", not a
reference value copied from a previous run — a number derivable on paper. A 3-4-5
triangle, two planes exactly 0.05 m apart, a 100 × 40 m rectangle whose volume is
200 m³. When a regression appears, this is the difference between knowing something
changed and knowing what the right answer was.

**The fake COM layer exists to test failure, not success.** Its job is the paths a
working machine never reaches: a call that succeeds without effect, a silently
ignored array layout, "busy" versus "not running". If you add handling for a new
COM quirk, add the fake-COM case that makes removing that handling turn the suite
red — the differences table in the README lists which ones already have one.

**Test files must not run anything at import time.** pytest imports a test module
during collection: module-level work runs there, with its output swallowed, and
contributes zero tests while the suite still reports green. Put shared setup in a
fixture and every assertion inside a `test_*` function. If you are unsure whether a
file is contributing, run `pytest --collect-only` and count.

**Claims in the documentation are tested where they can be.** The tool counts, the
tool lists, the number of rows in the differences table, the absence of
machine-specific paths — all assertions in `tests/`. A number repeated across
documents is not corroborated by being repeated: every copy comes from one
unverified count. The differences table had already drifted once, with the prose
saying "eight" beside a table of sixteen rows, seventy lines apart in the same file.

---

## Screenshots

If you change something a screenshot in `docs/images/` shows, regenerate it and
update its entry in [docs/images/README.md](docs/images/README.md). Read the rules
at the bottom of that file first — particularly the one about what a capture of a
live Civil 3D window discloses. The signed-in Autodesk account name sits in the top
right, and the tabs of every other open drawing sit under the ribbon.
