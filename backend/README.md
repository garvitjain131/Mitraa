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

### Adaptive desktop assistant

```powershell
python -m mitraa.assistant                     # hidden until explicitly summoned
python -m mitraa.assistant --preview           # completely offline UI demonstration
python -m mitraa.assistant --detect            # raw trigger key discovery; Esc stops
python -m mitraa.assistant --install-startup
python -m mitraa.assistant --uninstall-startup
```

Hold **Ctrl alone for three seconds**, or choose **Open (listen)** in the tray. Only a 72-logical-pixel glass orb appears, without stealing focus. Voice loading has a distinct circulating accent. Capture starts only once the local speech model is ready and playback has stopped; its microphone-driven glow indicates recording. There is no continuous hidden recording or automatic follow-up listening. Normal launch does not require provider keys.

Click the circle to reveal a 456-by-88 logical-pixel horizontal dock while the same recording continues. The same Orb instance and animation clock persist: internal teal currents turn clockwise in 14 seconds, amber currents counterclockwise in 22 seconds, and the glass boundary stays steady. The dock reveals to the right over 300 ms with width and opacity easing. It keeps the orb anchored where space permits and moves only enough to remain within the work area. Reduced motion disables rotation and expansion animation. These are procedural layers; the supplied reference PNGs are flattened and are not treated as separate light layers.

When speech ends, capture closes, the dock expands, and **Understanding…** appears. Local Whisper produces a final transcript, which submits to the existing agent once. The dock then displays actual tool progress and the response; long answers use an attached, scrollable card above it. The keyboard control replaces the central content with a focused input; Enter submits once. The microphone starts a fresh circle-only session when the agent is ready, stopping playback first. Repeated summon during work shows that work without submitting or recording again.

Escape or dismiss stops capture/playback and hides the interface. It does not cancel an in-flight file action. Agent results and conversation state survive hiding. Approvals retain their real approval ID until explicit Allow or Deny; hiding does not resolve them. Summoning a pending approval restores its card with action, paths/arguments, risk and reasons, with Deny initially focused. No animation, audio callback or presentation change executes tools.

Drag the orb to move it. Qt settings persist `adaptive_position` and `reduced_motion` separately for preview and normal mode. Placement uses the active Windows monitor and Qt logical work-area coordinates, including negative coordinates and taskbar exclusion. Visible shapes have an input mask, so transparent unused areas do not intercept clicks. Escape uses a temporary keyboard hook only while the normal overlay is visible; it is removed on hide/quit. Ctrl shortcut handling remains unchanged.

The tray contains Open (listen), New conversation, Mute speech output, Reduced motion, Pause hotkey and Quit. Change the trigger using `MITRAA_HOTKEY`; Ctrl+C and Ctrl+S never summon it. Only trigger/escape events are acted on; no keystrokes are stored. Administrator windows can prevent normal-process hooks from receiving keys.

#### API-free preview

Preview opens only the circle. Click to expand, or use tray **Preview: next / simulate speech end** to demonstrate endpoint expansion, understanding, thinking, tool progress, speaking appearance (without audio), response, error, approval, typing and a fresh circle. Choose Allow or Deny explicitly before leaving a preview approval. A separate preview tray typing action is also available. The window title, tooltip, tray and sample text label preview content. No provider client, model download, microphone, speech output or tools are constructed. Simulated states are not evidence of working audio. For a timed native entry-point check, run `python -m tests.preview_smoke` from `backend` (optionally set `QT_SCALE_FACTOR=1`, `1.5`, or `2`). This opens and closes preview automatically and saves rendered states under `backend/logs`.

#### Local speech detection and configuration

The single recorder uses [WebRTC VAD](https://github.com/daanzu/py-webrtcvad-wheels) with mono signed 16-bit PCM at 16 kHz and 30-ms frames. Native device-rate float audio is resampled with [SciPy polyphase filtering](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html), using preceding-frame filter context. Device callbacks only copy to a bounded 32-frame queue. Resampling, VAD, endpoint logic, transcription and Qt event signaling occur on a worker, outside the device callback. Queue/device overflows and VAD failures produce retry errors; there is no silent energy-only fallback.

| Environment setting | Default | Meaning |
|---|---:|---|
| `MITRAA_VAD_MODE` | 2 | WebRTC aggressiveness, 0–3 |
| `MITRAA_SPEECH_ONSET_MS` | 150 | Five positive VAD frames in a six-frame onset window |
| `MITRAA_MIN_SPEECH_MS` | 270 | Normal voiced-audio target; shorter confirmed utterances require stronger transcription confidence |
| `MITRAA_END_SILENCE` | 1.0 | Seconds of non-speech after confirmed speech; about 1.02 seconds at 30-ms resolution |
| `MITRAA_NO_SPEECH_TIMEOUT` | 8 | Capture seconds before speech must begin |
| `MITRAA_MAX_UTTERANCE` | 30 | Maximum capture seconds; accepted audio finalizes once with a visible limit notice |
| `MITRAA_PREROLL_MS` | 300 | Retained audio preceding onset, plus the onset-confirmation window |
| `MITRAA_TRAILING_MS` | 200 | Trailing audio kept after last VAD speech, rounded up to 210 ms |
| `MITRAA_STT_MIN_LOGPROB` | -1.0 | Minimum accepted Whisper segment average log probability |
| `MITRAA_STT_MAX_NO_SPEECH` | 0.6 | Maximum accepted Whisper no-speech probability |

VAD is the speech classifier; RMS supplies a low numerical-silence guard and a slowly adapting noise floor, updated only on non-speech frames (2% before onset, 0.2% afterward). There is no initial silent-calibration assumption, so immediate speech is retained. The onset window rejects brief bursts; a one-second end-silence threshold tolerates ordinary pauses. Brief confirmed speech is allowed when Whisper average log probability is at least -0.65. Segments with compression ratio above 2.4 are also excluded. Empty or rejected recordings never call the agent. These tunings do not guarantee speech/noise discrimination in every room.

Whisper runs locally (`MITRAA_STT_MODEL=small`, `MITRAA_STT_DEVICE=auto`, `MITRAA_STT_LANGUAGE=` for multilingual detection). Its first use may download the model; the loading orb remains responsive and capture stays off until readiness. No partial recognition is displayed in this version. Transcription and VAD are local, raw audio is not saved, and logs omit transcript/audio contents. Recognition timing depends on the hardware.

Speech output uses Windows SAPI (`MITRAA_TTS_VOICE=Heera`, `MITRAA_TTS_RATE=1`), with playback-state start, completion, interruption and error signals. The microphone waits for playback to stop. Muting affects speech output, not explicitly requested microphone input. Microphone errors include retry guidance; allow desktop microphone access in Windows settings yourself if necessary.

Qt owns the app/tray event loop. Agent/database sessions stay on one worker thread, with request-tagged results. Audio sessions have distinct IDs so cancelled capture, transcription and playback results cannot submit or modify a newer session. Streams close through context managers on every exit. Quit stops hooks, tray, capture and speech, releases the instance lock, and queues database cleanup on its owning thread. Already-running model/tool calls finish before that worker exits; neither tool execution nor Whisper inference is forcibly terminated.

Verification uses deterministic synthetic endpoint/frame tests, fake device streams for exit paths, confidence metadata fixtures and Qt state/focus/geometry tests. Native Windows Qt renders can verify component layout, but do not prove real-room speech accuracy, desktop click-through, cross-app focus or mixed-monitor DPI behavior. Those require hands-on checks on the target setup.

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
