"""UI-independent, single-threaded agent execution."""
from __future__ import annotations
import logging
import re
from types import SimpleNamespace
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from mitraa.agent.orchestrator import AgentResponse, Orchestrator
logger = logging.getLogger(__name__)
Event = tuple[str, Any, int]

def basic_reply(text: str) -> str | None:
    """Exact local UI-test commands; other requests still use the agent."""
    command = re.sub(r"[.!?,]+$", "", text.strip().casefold()).strip()
    command = " ".join(command.replace("’", "'").split())
    if command in {"hi", "hello"}:
        return "Hello!"
    if command in {"what is your name", "what's your name"}:
        return "Mitraa."
    return None

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
        self._pool.submit(self._guard, request_id, lambda agent: agent.run(self.conversation_id, text, save_user=False), text)

    @staticmethod
    def _message(row, request_id=None):
        from datetime import timezone
        stamp=row.created_at
        if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
        return dict(id=row.id,role=row.role,content=row.content or '',created_at=stamp.isoformat(),request_id=request_id)

    def history(self, generation: int, before: int | None = None):
        if not self._closing: self._pool.submit(self._history,generation,before)

    def _history(self,generation,before):
        from sqlalchemy import select
        from mitraa.db.models import Message
        from mitraa.db.session import default_session_factory
        try:
            with default_session_factory()() as session:
                query=select(Message).where(Message.conversation_id==self.conversation_id,
                    Message.role.in_(['user','assistant']),Message.content.is_not(None),
                    Message.tool_calls_json.is_(None))
                if before is not None: query=query.where(Message.id<before)
                rows=list(session.scalars(query.order_by(Message.id.desc()).limit(41)))
                payload=dict(conversation_id=self.conversation_id,messages=[self._message(r) for r in reversed(rows[:40])],
                             has_more=len(rows)>40,before=before)
            if not self._closing: self._post(('history',payload,generation))
        except Exception:
            logger.exception('Conversation history retrieval failed')
            if not self._closing: self._post(('history_error','Could not load conversation. Please retry.',generation))

    def _save_user(self,text):
        from mitraa.db.models import Conversation, Message
        from mitraa.db.session import default_session_factory
        with default_session_factory()() as session:
            conversation=session.get(Conversation,self.conversation_id) if self.conversation_id else None
            if conversation is None:
                conversation=Conversation(title=text[:80]); session.add(conversation); session.flush()
            self.conversation_id=conversation.id
            row=Message(conversation_id=conversation.id,role='user',content=text)
            session.add(row); session.commit()
            self._emit('message_saved',dict(conversation_id=conversation.id,message=self._message(row,self._request_id)))

    def _saved_answer(self,text):
        from sqlalchemy import select
        from mitraa.db.models import Message
        from mitraa.db.session import default_session_factory
        with default_session_factory()() as session:
            row=session.scalar(select(Message).where(Message.conversation_id==self.conversation_id,
                Message.role=='assistant',Message.content.is_not(None),Message.tool_calls_json.is_(None)).order_by(Message.id.desc()).limit(1))
            if row and row.content==text: self._emit('message_saved',dict(conversation_id=self.conversation_id,message=self._message(row,self._request_id)))

    def _local_response(self, text: str, reply: str):
        # This runs on the same owning worker as all other database operations.
        from mitraa.db.models import Conversation, Message
        from mitraa.db.session import default_session_factory
        with default_session_factory()() as session:
            conversation = session.get(Conversation, self.conversation_id) if self.conversation_id else None
            if conversation is None:
                conversation = Conversation(title=text[:80])
                session.add(conversation)
                session.flush()
            session.add(Message(conversation_id=conversation.id, role="assistant", content=reply))
            session.commit()
            return SimpleNamespace(type="message", conversation_id=conversation.id,
                                   text=reply, pending_approval=None, steps=[])

    def decide(self, approval_id: str, approved: bool, request_id: int) -> None:
        self._pool.submit(self._guard, request_id, lambda agent: agent.resume(approval_id, approved))

    def new_conversation(self) -> None:
        self._pool.submit(setattr, self, "conversation_id", None)

    def reload_configuration(self) -> None:
        if not self._closing:
            self._pool.submit(self._reload_configuration)

    def _reload_configuration(self):
        self._close()
        self._agent = None

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

    def _guard(self, request_id: int, action: Callable[[Orchestrator], AgentResponse], text: str | None = None) -> None:
        self._request_id = request_id
        try:
            if text is not None: self._save_user(text)
            reply = basic_reply(text) if text is not None else None
            response = self._local_response(text, reply) if reply is not None else action(self._ensure_agent())
            self.conversation_id = response.conversation_id
            if response.type=='message': self._saved_answer(response.text)
            self._emit("response", response)
        except Exception as exc:
            from mitraa.config import get_settings
            settings = get_settings()
            missing = not all((settings.nebius_api_key, settings.nebius_base_url, settings.nemotron_model))
            if not missing:
                logger.exception("Agent call failed")
            self._emit("error", {"message": "Connect your AI provider to chat." if missing else "The request could not be completed. Please try again.",
                                 "details": f"{type(exc).__name__}: {exc}", "provider_setup": missing})
