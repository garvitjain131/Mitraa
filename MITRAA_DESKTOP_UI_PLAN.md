# Mitraa adaptive desktop UI

This implementation supersedes the earlier vertical orb/pill design. Visual references are `design/mitraa-matching-standalone-circle.png` and `design/mitraa-expanded-dock.png`. They are references, not flattened interactive surfaces.

## Presentation and execution

Presentation has three states: hidden, circle_only, expanded_dock. Execution has idle, loading_voice, waiting_for_speech, recording_speech, transcribing, thinking, working, awaiting_approval, speaking and error. The Qt controller owns these independently.

Explicit Ctrl-alone hold (3 seconds), tray Open or microphone activation starts a circle-only session. Capture waits for model readiness, prior capture release and playback stop. Clicking the circle changes presentation only. A confirmed endpoint closes the stream before expanding and transcribing. A final validated transcript submits once. Repeated summon during an agent request reveals its progress. Typed input replaces the dock center. Long responses and explicit approvals use attached cards above the dock.

Escape hides and cancels audio; it does not claim running tools were cancelled or resolve approvals. Pending decisions and hidden agent results remain in controller/worker conversation state. A later summon restores an approval before any new recording can start.

## Components

- `assistant/utterance.py`: WebRTC VAD, configurable Endpointer, native-rate resampling, bounded callback queue, pre-roll and capture context.
- `assistant/voice.py`: cancellable model-readiness/capture/final-recognition cycle, Whisper confidence checks, Windows SAPI lifecycle.
- `assistant/worker.py`: serial request-tagged agent/orchestrator/database ownership and cleanup.
- `assistant/ui/controller.py`: session IDs, request IDs, explicit transitions, scoped Escape hook and Qt tray.
- `assistant/ui/overlay.py`: transparent masked shapes, 300-ms width/opacity reveal, work-area clamp, persisted position, cards and intentional input/approval focus.
- `assistant/ui/orb.py`: one 72-pixel shared procedural glass Orb. Independent teal/amber light currents use elapsed time (14/22-second opposite turns); the boundary stays fixed. The component/clock is never swapped during expansion.
- `assistant/ui/controls.py`: vector keyboard/microphone/dismiss icons and real-level waveform.

The dock is 456 by 88 logical pixels, with a dark translucent teal/amber fine edge. Its orb remains left-anchored where the monitor work area permits. When the right edge would overflow, position interpolates minimally during expansion. Work-area changes clamp the overlay. Reduced motion uses static light phases and immediate presentation changes.

## Endpoint defaults

WebRTC aggressiveness 2; 16-kHz mono PCM, 30-ms frames; onset 150 ms positive in a six-frame window; normal voiced target 270 ms; pre-roll 300 ms plus onset confirmation; end silence 1.0 s (frame-quantized); trailing padding 200 ms (210 ms quantized); no-speech timeout 8 s; capture limit 30 s. Immediate speech is never discarded for calibration. Noise adaptation uses only non-speech and slows after onset. Short confirmed utterances require stronger Whisper confidence rather than rejection by text length. Metadata filters: no-speech <=0.6, average log probability >=-1.0 (-0.65 for short speech), compression ratio <=2.4.

All endpoint settings and their `MITRAA_*` names appear in `backend/.env.example` and `backend/README.md`. WebRTC failure is an explicit error, never an energy-only fallback. The callback only copies native-rate mono frames to a bounded queue; resampling/VAD/transcription never run inside it. Streams close before final recognition and on cancellation/errors. Audio is kept in memory only.

## Preview and checks

`python -m mitraa.assistant --preview` starts circle-only. Click it to expand. Tray preview actions demonstrate simulated endpoint, thinking, working, speaking appearance without audio, responses, errors, approval and typing. Sample text and tray/title/tooltip are labeled. A preview approval requires an explicit decision; no agent/audio/provider/model/download/tool is created.

Automated tests cover endpoint onset, pre-roll, brief pauses, noise bursts, timeout, limit, finalization, stream exit paths, resampling, metadata rejection, loading/playback/cancellation gating, stale session/request events, duplicate submissions, click-only presentation changes, pending approvals, anchored/clamped geometry, cards and existing hotkey/backend security behavior.

Native Qt renders inspect shape/layout on Windows. Actual room speech/fan noise, cross-app keyboard focus, desktop click-through and mixed-DPI/negative-monitor transitions need hands-on verification and must not be inferred from synthetic tests or preview. Speech models and tools already running cannot be forcibly cancelled; their late UI events are invalidated and owning workers clean up when they return.
