"""UI-independent, single-threaded agent execution."""
from __future__ import annotations
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from mitraa.agent.orchestrator import AgentResponse, Orchestrator
logger = logging.getLogger(__name__)
Event = tuple[str, Any, int]

class AgentWorker:
    """Runs the agent on one background thread so the UI never blocks.

    The DB session and Orchestrator are created and used only on that thread.
    """

    def __init__(self, post: Callable[[Event], None]) -> None:
        self._post = post
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mitraa-agent")
        self._agent: Orchestrator | None = None
        self.conversation_id: str | None = None
        self._request_id = 0
        self._closing = False

    def send(self, text: str, request_id: int) -> None:
        self._pool.submit(self._guard, request_id, lambda agent: agent.run(self.conversation_id, text))

    def decide(self, approval_id: str, approved: bool, request_id: int) -> None:
        self._pool.submit(self._guard, request_id, lambda agent: agent.resume(approval_id, approved))

    def new_conversation(self) -> None:
        self._pool.submit(setattr, self, "conversation_id", None)

    def shutdown(self) -> None:
        if self._closing: return
        self._closing = True
        self._pool.submit(self._close)
        self._pool.shutdown(wait=False, cancel_futures=False)

    def _close(self) -> None:
        if self._agent is not None:
            self._agent.session.close()

    def _ensure_agent(self) -> Orchestrator:
        if self._agent is None:
            from mitraa.agent.llm import NemotronClient
            from mitraa.agent.orchestrator import Orchestrator
            from mitraa.config import get_settings
            from mitraa.db.session import default_session_factory
            settings = get_settings()
            llm = NemotronClient(settings)
            session = default_session_factory()()
            try:
                self._agent = Orchestrator(llm, session, settings, on_step=lambda step: self._emit("step", step))
            except Exception:
                session.close()
                raise
        return self._agent

    def _emit(self, kind: str, data: Any) -> None:
        if not self._closing:
            self._post((kind, data, self._request_id))

    def _guard(self, request_id: int, action: Callable[[Orchestrator], AgentResponse]) -> None:
        self._request_id = request_id
        try:
            response = action(self._ensure_agent())
            self.conversation_id = response.conversation_id
            self._emit("response", response)
        except Exception as exc:
            from mitraa.config import get_settings
            settings = get_settings()
            missing = not all((settings.nebius_api_key, settings.nebius_base_url, settings.nemotron_model))
            if not missing:
                logger.exception("Agent call failed")
            self._emit("error", {"message": "Connect your AI provider to chat." if missing else "The request could not be completed. Please try again.",
                                 "details": f"{type(exc).__name__}: {exc}", "provider_setup": missing})
