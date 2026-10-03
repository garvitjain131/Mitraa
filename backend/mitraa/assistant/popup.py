"""The summoned pop-up: a small always-on-top chat window backed by the Orchestrator."""

from __future__ import annotations

import logging
import queue
import sys
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mitraa.agent.llm import LLMError, NemotronClient
from mitraa.agent.orchestrator import AgentResponse, Orchestrator, Step
from mitraa.assistant.voice import Voice, VoiceInput
from mitraa.config import get_settings
from mitraa.db.session import default_session_factory

logger = logging.getLogger(__name__)

COLORS = {
    "bg": "#17181c",
    "panel": "#202227",
    "text": "#e8e9ec",
    "muted": "#8b8f98",
    "accent": "#7c9cff",
    "ok": "#5fd38d",
    "error": "#ff6b6b",
    "warn": "#ffb454",
    "input": "#2a2c33",
}
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
STEP_ICONS = {"success": "✓", "error": "✗", "blocked": "⛔", "denied": "✗", "pending": "⏳"}

Event = tuple[str, Any]


class AgentWorker:
    """Runs the agent on one background thread so the UI never blocks.

    The DB session and Orchestrator are created and used only on that thread.
    """

    def __init__(self, post: Callable[[Event], None]) -> None:
        self._post = post
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mitraa-agent")
        self._agent: Orchestrator | None = None
        self.conversation_id: str | None = None

    def send(self, text: str) -> None:
        self._pool.submit(self._guard, lambda agent: agent.run(self.conversation_id, text))

    def decide(self, approval_id: str, approved: bool) -> None:
        self._pool.submit(self._guard, lambda agent: agent.resume(approval_id, approved))

    def new_conversation(self) -> None:
        self._pool.submit(setattr, self, "conversation_id", None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _ensure_agent(self) -> Orchestrator:
        if self._agent is None:
            settings = get_settings()
            llm = NemotronClient(settings)
            session = default_session_factory()()
            self._agent = Orchestrator(llm, session, settings, on_step=lambda step: self._post(("step", step)))
        return self._agent

    def _guard(self, action: Callable[[Orchestrator], AgentResponse]) -> None:
        try:
            response = action(self._ensure_agent())
            self.conversation_id = response.conversation_id
            self._post(("response", response))
        except LLMError as exc:
            self._post(("error", f"{exc}\nAdd your Nebius settings to backend/.env and restart."))
        except Exception as exc:  # keep the assistant alive whatever happens
            logger.exception("Agent call failed")
            self._post(("error", f"Something went wrong: {type(exc).__name__}: {exc}"))


class AssistantWindow:
    def __init__(self, root: tk.Tk, hotkey_hint: str, voice: Voice | None = None) -> None:
        self.root = root
        self.hotkey_hint = hotkey_hint
        self.events: queue.Queue[Event] = queue.Queue()
        self.worker = AgentWorker(self.events.put)
        self.on_quit: Callable[[], None] | None = None
        self.speaker = voice.speaker if voice else None
        self.voice_input = VoiceInput(voice.transcriber, self.events.put) if voice and voice.transcriber else None
        self._pending_approval: str | None = None
        self._busy = False
        self._build()
        self.win.withdraw()
        root.after(50, self._drain)

    # ------------------------------------------------------------ window

    def _build(self) -> None:
        c = COLORS
        win = self.win = tk.Toplevel(self.root, bg=c["bg"])
        win.title("Mitraa")
        win.geometry("620x480")
        win.minsize(420, 340)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.hide)

        header = tk.Frame(win, bg=c["bg"])
        header.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(header, text="What do you want?", font=(FONT, 16, "bold"), fg=c["text"], bg=c["bg"]).pack(side="left")
        self._flat_button(header, "New chat", self.new_chat).pack(side="right")
        if self.speaker:
            self.mute_button = self._flat_button(header, "🔊 Voice on", self.toggle_mute)
            self.mute_button.pack(side="right", padx=(0, 8))

        bottom = tk.Frame(win, bg=c["bg"])
        bottom.pack(fill="x", side="bottom", padx=16, pady=(8, 14))
        row = tk.Frame(bottom, bg=c["bg"])
        row.pack(fill="x")
        if self.voice_input:
            self.mic_button = tk.Button(
                row, text="🎤", command=self.start_listening, font=(FONT, 13), bg=c["input"], fg=c["text"],
                activebackground=c["accent"], activeforeground="#111", relief="flat", padx=10, cursor="hand2",
            )
            self.mic_button.pack(side="right", fill="y", padx=(8, 0))
        self.entry = tk.Entry(
            row, font=(FONT, 12), bg=c["input"], fg=c["text"], insertbackground=c["text"],
            disabledbackground=c["panel"], disabledforeground=c["muted"],
            relief="flat", highlightthickness=1, highlightcolor=c["accent"], highlightbackground=c["input"],
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.meter = tk.Canvas(bottom, height=4, bg=c["bg"], highlightthickness=0)
        self.meter.pack(fill="x", pady=(4, 0))
        self.meter_bar = self.meter.create_rectangle(0, 0, 0, 4, fill=c["accent"], width=0)
        self.status = tk.Label(bottom, text=self._idle_status(), anchor="w", font=(FONT, 9), fg=c["muted"], bg=c["bg"])
        self.status.pack(fill="x", pady=(2, 0))

        body = self.body = tk.Frame(win, bg=c["panel"])
        body.pack(fill="both", expand=True, padx=16)
        self.transcript = tk.Text(
            body, wrap="word", bg=c["panel"], fg=c["text"], font=(FONT, 11), relief="flat",
            padx=12, pady=10, state="disabled", cursor="arrow", highlightthickness=0,
        )
        scroll = tk.Scrollbar(body, command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.transcript.pack(side="left", fill="both", expand=True)
        self.transcript.tag_configure("you", foreground=c["accent"], font=(FONT, 11, "bold"), spacing1=8)
        self.transcript.tag_configure("mitraa", foreground=c["text"], spacing1=4, spacing3=6)
        self.transcript.tag_configure("step", foreground=c["muted"], font=(FONT, 9), lmargin1=14, lmargin2=28)
        self.transcript.tag_configure("error", foreground=c["error"], spacing1=4)
        self.transcript.tag_configure("hint", foreground=c["muted"], font=(FONT, 10, "italic"))

        self.approval = tk.Frame(win, bg=c["bg"])
        self.approval_text = tk.Label(
            self.approval, text="", justify="left", anchor="w", wraplength=560,
            font=(FONT, 10), fg=c["warn"], bg=c["bg"],
        )
        self.approval_text.pack(fill="x", pady=(8, 6))
        buttons = tk.Frame(self.approval, bg=c["bg"])
        buttons.pack(anchor="e")
        self.deny_button = self._button(buttons, "Deny", lambda: self._decide(False), c["input"], c["text"])
        self.allow_button = self._button(buttons, "Allow", lambda: self._decide(True), c["warn"], "#1a1a1a")
        self.deny_button.pack(side="left", padx=(0, 8))
        self.allow_button.pack(side="left")

        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<Key>", self._on_typing)
        win.bind("<Escape>", lambda _e: self.dismiss())
        win.bind("<Control-n>", lambda _e: self.new_chat())
        talk = "talk or type" if self.voice_input else "type"
        self._append(f"Hold {self.hotkey_hint} for 3 seconds anytime, then {talk}.\n", "hint")

    def _flat_button(self, parent: tk.Widget, text: str, command: Callable[[], None]) -> tk.Button:
        c = COLORS
        return tk.Button(
            parent, text=text, command=command, font=(FONT, 9), fg=c["muted"], bg=c["panel"],
            activebackground=c["input"], activeforeground=c["text"], relief="flat", padx=10, cursor="hand2",
        )

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None], bg: str, fg: str) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, font=(FONT, 10, "bold"), bg=bg, fg=fg,
            activebackground=bg, activeforeground=fg, relief="flat", padx=16, pady=4, cursor="hand2",
        )
        button.bind("<Return>", lambda _e: command())
        return button

    def show(self) -> None:
        logger.info("Showing window")
        self.win.deiconify()
        self.win.update_idletasks()
        width, height = self.win.winfo_width(), self.win.winfo_height()
        x = (self.win.winfo_screenwidth() - width) // 2
        y = (self.win.winfo_screenheight() - height) // 3
        self.win.geometry(f"+{x}+{y}")
        self.win.lift()
        self.win.attributes("-topmost", True)
        _force_foreground(self.win)
        (self.deny_button if self._pending_approval else self.entry).focus_force()

    def hide(self) -> None:
        self.win.withdraw()

    def dismiss(self) -> None:
        """Esc: stop listening and talking, then hide."""
        if self.voice_input:
            self.voice_input.cancel()
        if self.speaker:
            self.speaker.stop()
        self.hide()

    def new_chat(self) -> None:
        if self._busy:
            return
        self.worker.new_conversation()
        self._hide_approval()
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self._append("New conversation.\n", "hint")
        self.entry.focus_set()

    def toggle_mute(self) -> None:
        if not self.speaker:
            return
        self.speaker.enabled = not self.speaker.enabled
        if not self.speaker.enabled:
            self.speaker.stop()
        self.mute_button.configure(text="🔊 Voice on" if self.speaker.enabled else "🔇 Muted")

    # ------------------------------------------------------------ voice

    def start_listening(self) -> None:
        if not self.voice_input or self._busy or self._pending_approval or self.voice_input.active:
            return
        if self.speaker:
            self.speaker.stop()  # don't transcribe Mitraa's own voice
        self.voice_input.start()

    def _on_typing(self, event: tk.Event) -> None:
        if self.voice_input and self.voice_input.active and event.keysym not in ("Escape", "Return"):
            self.voice_input.cancel()  # typing instead of talking

    def _set_level(self, rms: float) -> None:
        width = self.meter.winfo_width()
        fraction = min(1.0, (rms / 0.08) ** 0.5)
        self.meter.coords(self.meter_bar, 0, 0, int(width * fraction), 4)

    def _voice_state(self, state: str) -> None:
        listening = state == "listening"
        texts = {
            "listening": "🎙 Listening… speak now  ·  Esc to stop  ·  start typing to type instead",
            "transcribing": "Understanding…",
            "loading": "Loading the speech model (first time only)…",
        }
        if state in texts:
            self.status.configure(text=texts[state], fg=COLORS["accent"])
        else:
            self.status.configure(text=self._idle_status(), fg=COLORS["muted"])
        if not listening:
            self._set_level(0)
        if self.voice_input:
            self.mic_button.configure(bg=COLORS["accent"] if listening else COLORS["input"])

    def _idle_status(self) -> str:
        talk = f"Hold {self.hotkey_hint} or 🎤 to talk · " if self.voice_input else ""
        return f"{talk}Enter to send · Esc to hide · Ctrl+N new chat"

    # ------------------------------------------------------------ actions

    def submit(self, text: str | None = None) -> None:
        text = (text if text is not None else self.entry.get()).strip()
        if not text or self._busy or self._pending_approval:
            return
        self.entry.delete(0, "end")
        self._append(f"You: {text}\n", "you")
        self._set_busy(True)
        self.worker.send(text)

    def _decide(self, approved: bool) -> None:
        approval_id = self._pending_approval
        if approval_id is None:
            return
        self._hide_approval()
        self._append("✓ You allowed it.\n" if approved else "✗ You denied it.\n", "step")
        self._set_busy(True)
        self.worker.decide(approval_id, approved)

    # ------------------------------------------------------------ events from other threads

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(30, self._drain)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "summon":
            self.show()
            if payload != "tray":
                self.start_listening()
        elif kind == "voice_state":
            self._voice_state(payload)
        elif kind == "voice_level":
            self._set_level(payload)
        elif kind == "voice_text":
            self._voice_state("idle")
            self.submit(payload)
        elif kind == "voice_none":
            self._voice_state("idle")
            self._append(f"{payload} Hold {self.hotkey_hint} or click 🎤 to try again.\n", "hint")
        elif kind == "voice_error":
            self._voice_state("idle")
            self._append(f"{payload}\n", "error")
        elif kind == "step":
            step: Step = payload
            if step.status != "pending":
                self._append(f"{STEP_ICONS.get(step.status, '•')} {step.tool}: {step.summary}\n", "step")
        elif kind == "response":
            response: AgentResponse = payload
            self._set_busy(False)
            if response.type == "approval_required" and response.pending_approval:
                self._show_approval(response)
            else:
                self._append(f"Mitraa: {response.text}\n", "mitraa")
                self._say(response.text)
                self.entry.focus_set()
        elif kind == "error":
            self._set_busy(False)
            self._append(f"{payload}\n", "error")
        elif kind == "quit" and self.on_quit:
            self.on_quit()

    def _say(self, text: str) -> None:
        if self.speaker:
            self.speaker.say(text)

    def _show_approval(self, response: AgentResponse) -> None:
        info = response.pending_approval
        assert info is not None
        self._pending_approval = info.approval_id
        lines = [f"⚠  Mitraa wants to: {info.description}", f"Risk: {info.risk}"]
        lines += [f"• {reason}" for reason in info.reasons]
        self.approval_text.configure(text="\n".join(lines))
        self.approval.pack(fill="x", padx=16, side="bottom", before=self.body)
        self.entry.configure(state="disabled")
        self.show()  # bring it forward if the user hid it while waiting
        self.deny_button.focus_force()
        self._say(f"I need your permission to {info.description}. Please choose Allow or Deny.")

    def _hide_approval(self) -> None:
        self._pending_approval = None
        self.approval.pack_forget()
        self.entry.configure(state="normal")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.entry.configure(state="disabled" if busy or self._pending_approval else "normal")
        self.status.configure(
            text="Working…" if busy else self._idle_status(),
            fg=COLORS["accent"] if busy else COLORS["muted"],
        )

    def _append(self, text: str, tag: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")


def _force_foreground(win: tk.Toplevel) -> None:
    """Windows blocks background apps from stealing focus; attach to the foreground thread first."""
    if sys.platform != "win32":
        win.focus_force()
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]

        hwnd = int(win.wm_frame(), 16)
        foreground = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(foreground, None)
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = fg_thread and fg_thread != our_thread and user32.AttachThreadInput(fg_thread, our_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(fg_thread, our_thread, False)
    except Exception:  # focus is best-effort
        logger.debug("Could not force foreground", exc_info=True)
