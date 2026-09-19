# Security Policy

## Trust model — read this before you connect the server

This MCP server gives a language model **direct control of Autodesk Civil 3D on
your machine**. That is the point of the project, and it is also the main thing to
be aware of:

| Capability | What it means in practice |
|---|---|
| `send_civil3d_command` | Arbitrary AutoCAD and Civil 3D commands run inside your session |
| `delete_surface` | A surface can be deleted; over COM this is not undoable |
| `save_drawing`, `open_drawing` | Drawings on disk can be overwritten or opened |
| `create_*`, `add_*`, `rebuild_*` | The open drawing is modified in place |
| `export_*` | CSV and Markdown files are written to paths the model chooses |
| `launch_civil3d` | Civil 3D is started on your desktop |

There is **no sandbox and no confirmation prompt inside the server** beyond the
`confirm` flag on deleting tools. Every other guard rail lives in your MCP client.
So:

- **Only connect this server to an MCP client you trust**, and keep that client's
  tool-approval prompts on rather than blanket-approving everything.
- **Work on copies of drawings** while you are getting used to it. `_UNDO` covers a
  lot inside a session, but not a `save_drawing` that already happened, and Civil 3D
  objects deleted over COM do not always come back cleanly.
- **The server acts on the ACTIVE drawing.** If you have a production drawing open
  in the same Civil 3D session, a tool call can modify it. `get_drawing_info` and
  `list_open_drawings` tell you what is open; check before a batch of write calls.
- **Survey and design data is usually commercially sensitive**, and often
  identifies a real site down to the coordinate. Think about that before pointing
  the model at a folder, and before pasting tool output into a shared conversation
  — a surface's bounds are its location.
- Treat drawing content as untrusted input if it came from outside: names,
  descriptions and text inside a DWG are free text that the model will read, and
  can carry prompt-injection payloads.

The server opens no network port and sends your data nowhere.

## A note on screenshots

If you are preparing images for a public issue or a pull request, read the rules at
the end of [docs/images/README.md](docs/images/README.md). A capture of a live
Civil 3D window contains the **signed-in Autodesk account name** in the top right
and **a tab for every other drawing open in that session** under the ribbon —
neither of which is the subject of the image, and both of which are permanent once
committed.

## Reporting a vulnerability

Please report privately rather than in a public issue:

- Use GitHub's **[Report a vulnerability](https://github.com/xuantinhnbs-rgb/civil3d-mcp/security/advisories/new)**
  form on this repository.

Include what an attacker would gain and how to reproduce it. You will get an
acknowledgement, and credit in the fix unless you prefer otherwise.

### What is in scope

- A tool that writes or executes outside what its arguments describe.
- Argument handling that lets crafted input reach the AutoCAD command line as
  something other than the intended value.
- A deleting tool that acts without its `confirm` flag.
- Anything that sends local data off the machine.

### What is not

- The model being able to modify the open drawing. That is the documented purpose
  of the server; the control for it is your MCP client's approval prompts.
- Autodesk Civil 3D's own behaviour, including the COM limits listed in the README.
  Report those to Autodesk.
