# Mitraa backend

Mitraa is a local-first personal AI agent. It runs on your machine, talks to NVIDIA Nemotron through Nebius Token Factory, and acts on your files through validated, permission-checked and audited tool calls.

**Phase 2 status:** persistent memory and deterministic project planning are implemented alongside the Phase 1 agent and file controls. Research and the web UI are planned for Phases 3–4 (see `../NOVA_BUILD_PLAN.md`).

## Setup

Requires Python 3.11 or newer.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
# then edit .env (see below)
```

### `.env`

| Variable | Meaning |
|---|---|
| `NEBIUS_API_KEY` | Your Nebius Token Factory API key |
| `NEBIUS_BASE_URL` | The OpenAI-compatible endpoint from the Nebius console (ends in `/v1/`) |
| `NEMOTRON_MODEL` | The exact Nemotron model id from the Nebius catalog |
| `MITRAA_ALLOWED_ROOTS` | Semicolon-separated folders Mitraa may touch. Default: `~/Documents;~/Downloads;~/Desktop`. On Windows, folders redirected to OneDrive are found automatically |
| `MITRAA_MAX_STEPS` | Maximum LLM calls per request (default 8) |
| `MITRAA_BULK_THRESHOLD` | Actions touching more than this many files need approval (default 5) |
| `MITRAA_DB_URL` | Optional SQLite URL. Default: `backend/mitraa.db` |

`.env` is gitignored. Never commit keys.

## Run

```powershell
# Terminal chat (best way to test Phase 1)
python -m mitraa.cli

# API server on http://127.0.0.1:8000 (bound to localhost only)
python -m mitraa.main

# Tests
python -m pytest
```

### Example CLI session

```
you> Move resume.pdf from Documents to Downloads
  ✓ search_files [LOW] Found 1 match(es) for 'resume.pdf'.
  ✓ move_file [MEDIUM] Moved resume.pdf to C:\Users\you\Downloads. Say 'undo' to revert.
mitraa> Moved resume.pdf to Downloads. Say 'undo' to revert.

you> undo that
  ✓ undo_last_action [MEDIUM] Moved resume.pdf back to C:\Users\you\Documents.

you> Delete old_notes.txt
  ✓ search_files [LOW] Found 1 match(es) for 'old_notes.txt'.
  ⏳ delete_file [HIGH] Waiting for user approval.
  ⚠  Mitraa wants to: Delete C:\Users\you\Documents\old_notes.txt (moves it to the Recycle Bin)
  Allow? [y/N] y
  ✓ delete_file [HIGH] Moved old_notes.txt to the Recycle Bin.
```

CLI commands: `/new` starts a new conversation, `/audit` shows recent tool calls, and `/quit` exits.

### Phase 2: memory and projects

Mitraa stores lasting facts across conversations in SQLite. In chat, try:

```text
My hackathon project is called NOVA.
What am I building?
Forget my project name.
Create a project for the NVIDIA hackathon. I have 10 days and am working alone.
Generate a task plan for it.
What's the critical path?
I have 3 days left. Optimize the schedule.
What if I lose a day?
What should I work on now?
```

Project plans contain estimates and dependencies; Python validates cycles, computes CPM and uses OR-Tools CP-SAT to assign hourly work slots around each member's daily capacity and time off. A 10-second solver limit may return `FEASIBLE` instead of `OPTIMAL`. The scheduler plans up to 365 days ahead and rounds task estimates up to whole work hours. Weekends count as working days unless `weekends_working` is changed on the project.

The HTTP API also supports direct project and memory management. For example:

```powershell
$project = Invoke-RestMethod http://127.0.0.1:8000/projects -Method Post -ContentType application/json -Body '{"name":"NOVA","goal":"Finish the hackathon demo","deadline_days":10}'
Invoke-RestMethod "http://127.0.0.1:8000/projects/$($project.id)/schedule"
Invoke-RestMethod "http://127.0.0.1:8000/projects/$($project.id)/simulate" -Method Post -ContentType application/json -Body '{"type":"member_unavailable","member":"You","days":1}'
```

### Desktop assistant (hold a key to summon)

```powershell
python -m mitraa.assistant                     # runs in the background with a tray icon
python -m mitraa.assistant --detect            # shows raw key codes (for choosing a different key)
python -m mitraa.assistant --install-startup   # start automatically with Windows
python -m mitraa.assistant --uninstall-startup
```

Hold **Ctrl** (left or right) on its own for 3 seconds and a "What do you want?" window opens on top of everything. Type a request and press Enter. Steps appear live, and risky actions show **Allow / Deny** buttons (Deny has focus by default). Press Esc to hide the window and Ctrl+N to start a new chat. The tray icon has Open, Pause hotkey and Quit.

- **Changing the key:** set `MITRAA_HOTKEY` in `.env`, e.g. `ctrl_r`, `menu`, `pause`, `f12` or a raw code like `vk:255`. Fn can't be used on this laptop because the keyboard never sends it to Windows.
- **Shortcuts are safe:** Ctrl only counts when held alone. Ctrl+C, Ctrl+S and other shortcuts never trigger the assistant, even if you keep Ctrl down afterwards.
- **`--detect` sees every key you press** while it runs. Use it briefly and press Esc to stop it.

#### Voice (Siri-style, without a wake word)

Hold Ctrl for 3 seconds, wait for "🎙 Listening…", and speak normally. When you stop talking, Mitraa transcribes what you said, acts on it, and answers out loud. There's no "Hey Mitraa": the microphone is only on after you summon it, and a blue meter under the text box shows when it's listening.

| Piece | How it works |
|---|---|
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper), `small` multilingual model, **running locally**: audio never leaves the PC. It handles English, Hindi and Hinglish (auto-detected) |
| Speed | About 0.1 s on an NVIDIA GPU (`pip install -r requirements-gpu.txt`), about 2–3 s on the CPU. It picks the GPU automatically when available |
| End of speech | It learns the room's noise level, starts when you speak, and stops after about 1.1 s of silence (maximum 20 s) |
| Text-to-speech | Windows voices through SAPI, including the newer OneCore voices. The default is **Heera (English, India)**. Paths, links and markdown are stripped, and long replies are cut short ("The rest is on screen.") |
| Approvals | Risky actions are announced out loud, but still need a click on **Allow** |

Controls: 🎤 listens again, 🔊/🔇 mutes the voice, typing cancels listening, and Esc stops both listening and speaking. Hindi replies come back in Roman script (Hinglish) so the voice can read them; install a Hindi voice in Windows if you want Devanagari.

If the microphone doesn't work, allow desktop apps to use it in **Settings → Privacy & security → Microphone**.
- **Privacy:** only the trigger keys are acted on. No keystrokes are stored or logged.
- **Admin windows:** while an administrator window (such as Task Manager) has focus, Windows hides key events from normal programs, so the hotkey won't fire until you click elsewhere.
- The assistant uses the same agent, sandbox, approvals and audit log as the CLI and API. Logs go to `backend/logs/assistant.log`.

### API

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/chat` | `{conversation_id?, message}` | `AgentResponse` |
| POST | `/chat/approve` | `{approval_id, approved}` | `AgentResponse` (409 if already resolved) |
| GET | `/conversations` | | recent conversations |
| GET | `/conversations/{id}` | | conversation with all messages |
| GET | `/tools` | | name, description, risk and JSON schema of each tool |
| GET | `/audit` | `?limit=50&conversation_id=` | audit log, newest first |
| GET | `/health` | | `{status, version}` |
| GET/POST/DELETE | `/memory`, `/memory/{id}` | type, key, value | Saved memories |
| GET/POST/PATCH/DELETE | `/projects`, `/projects/{id}` | name, goal, deadline_days, team; DELETE requires `confirmed=true` | Project details |
| GET | `/projects/{id}/schedule` | | Gantt-ready dates, dependencies, assignees, critical path and slack |
| POST | `/projects/{id}/simulate` | Scenario JSON | Baseline, changed schedule and date/task diff |
| GET/POST/PATCH/DELETE | `/tasks`, `/tasks/{id}` | Task details | Project tasks |

`AgentResponse`:

```json
{
  "type": "message | approval_required",
  "conversation_id": "…",
  "text": "Moved resume.pdf to Downloads.",
  "pending_approval": {"approval_id": "…", "tool": "delete_file", "args": {}, "risk": "HIGH",
                       "description": "Delete … (moves it to the Recycle Bin)", "reasons": ["…"]},
  "steps": [{"id": "…", "tool": "move_file", "args": {}, "risk": "MEDIUM", "status": "success", "summary": "…"}]
}
```

```powershell
curl -X POST http://127.0.0.1:8000/chat -H "content-type: application/json" -d '{"message":"What is in my Downloads?"}'
```

## How it works

```
user message
   → Orchestrator (agent/orchestrator.py)         loop ≤ MITRAA_MAX_STEPS LLM calls
      → Nemotron (agent/llm.py)                   temperature 0.2, 60s timeout, 2 retries
      → tool call
         → parse + pydantic validation            bad args → "Invalid arguments … Fix and retry." (one repair)
         → permissions.evaluate (security/)       sandbox + executables + overwrite + bulk count
              LOW / MEDIUM → execute (undoable)
              HIGH         → PendingApproval, loop pauses until /chat/approve
              BLOCKED      → refused, the reason goes back to the model
         → audit log (tool_calls table)
         → tool result appended → next LLM call
```

### Security model

- **Sandbox** (`security/sandbox.py`): every path is expanded, resolved (following symlinks and junctions) and must sit inside an allowed root (`Path.is_relative_to`). The sandbox also rejects `..` traversal, system folders, reserved Windows device names (`CON`, `NUL`, …), alternate data streams, and relative paths that don't start with an alias such as `Documents/…`.
- **Permission tiers** (`security/permissions.py`):

  | Tier | Tools / conditions |
  |---|---|
  | LOW | `search_files`, `list_folder`, `read_file` |
  | MEDIUM | `move_file`, `move_files`, `copy_file`, `rename_file`, `create_folder`, `undo_last_action` |
  | HIGH | `delete_file`, `delete_files`, anything that would overwrite, anything touching more than `MITRAA_BULK_THRESHOLD` files |
  | BLOCKED | paths outside the roots, system folders, executables/scripts (`.exe`, `.bat`, `.ps1`, …), the root folders themselves |

- **No permanent deletes:** deletes and overwrites go through `send2trash`, and tool code contains no `os.remove`, `unlink` or `rmtree` (a test enforces this).
- **Undo:** each change is recorded in `file_actions`. `undo_last_action` reverses the most recent change in the conversation. Deleted items must be restored from the Recycle Bin.
- **Audit:** every tool call, including invalid, blocked and denied ones, is written to `tool_calls` with args, risk, status, summary and duration.

## Adding a tool

```python
class MyArgs(BaseModel):
    path: str

class MyTool(Tool):
    name = "my_tool"
    description = "What it does, when to use it, when not to."
    args_model = MyArgs
    base_risk = RiskLevel.LOW

    def impact(self, args, ctx):          # optional: paths/count/overwrites for the permission engine
        return Impact(paths=[ctx.sandbox.resolve(args.path)])

    def run(self, args, ctx) -> ToolResult:
        ...

registry.register(MyTool())
```

## Phase 1 acceptance tests

| # | Test | Automated in |
|---|---|---|
| 1 | Move resume.pdf from Documents to Downloads | `test_orchestrator.py::test_move_end_to_end` |
| 2 | Ambiguous "move resume" makes Mitraa ask | `test_ambiguous_search_returns_both_matches` (the asking itself is the model's job, driven by the system prompt) |
| 3 | Delete needs approval, then goes to the Recycle Bin | `test_delete_requires_approval_then_trashes`, `test_denied_action_is_not_executed` |
| 4 | Undo restores the last move | `test_undo_in_follow_up_message`, `test_file_tools.py::test_undo_*` |
| 5 | System paths and `..\..\` are refused | `test_sandbox.py`, `test_escape_is_blocked_and_reported` |
| 6 | Every action is in `GET /audit` | `test_every_call_is_audited`, `test_api.py` |
| 7 | Stops after `MAX_STEPS` | `test_max_steps_enforced`, `test_max_steps_counts_across_approval` |
| 8 | `pytest` passes, including sandbox escapes | `python -m pytest` |

## Known limitations (Phase 1)

- `read_file` reads plain text only; PDF and DOCX reading arrives in Phase 3.
- Deletes can't be undone by Mitraa; restore them from the Recycle Bin.
- `search_files` matches names, not contents, and stops after 20,000 entries.
- Symlink-escape tests are skipped on Windows unless Developer Mode allows creating symlinks. Junction escapes are tested instead.
- A request blocks until the model finishes (no streaming yet; that comes in Phase 4).
