"""User session and project management for Mattermost bot with backend support."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECTS_FILE = Path(__file__).parent.parent / "data" / "projects.json"
STATE_FILE = Path(__file__).parent.parent / "data" / "state.json"

DEFAULT_PROJECTS = {
    "book": {
        "name": "📖 Книга «Оно сломалось»",
        "path": "/home/p_tikhomirov/projects/ono_slomalos",
        "channel": "book",
        "backend": "qwen",
    },
    "server": {
        "name": "🖥️ Домашний сервер",
        "path": "/home/p_tikhomirov/go/sandbox/linux_workstation_server",
        "channel": "server",
        "backend": "qwen",
    },
    "secretar": {
        "name": "🤖 AI-секретарь",
        "path": "/home/p_tikhomirov/projects/ai-secretar",
        "channel": "secretar",
        "backend": "qwen",
    },
    "admin": {
        "name": "⚙️ Управление ботом",
        "path": str(Path(__file__).parent.parent.resolve()),
        "channel": "admin",
        "backend": "qwen",
    },
}


def _load_projects() -> dict:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text())
        except Exception:
            logger.exception("Failed to load projects.json")
    projects_json = os.environ.get("PROJECTS_JSON")
    if projects_json:
        return json.loads(projects_json)
    save_projects(DEFAULT_PROJECTS)
    return DEFAULT_PROJECTS


def save_projects(projects: dict) -> None:
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2))
    logger.info("Projects saved to %s", PROJECTS_FILE)


def reload_projects() -> dict:
    global PROJECTS
    PROJECTS = _load_projects()
    return PROJECTS


PROJECTS = _load_projects()


def project_for_channel(channel_name: str) -> str | None:
    for key, proj in PROJECTS.items():
        if proj.get("channel") == channel_name:
            return key
    return None


@dataclass
class ThreadSession:
    """An AI assistant session tied to a Mattermost thread."""
    session_id: str = ""
    mode: str = "discuss"
    backend: str = ""  # which backend created this session (claude/qwen)


@dataclass
class UserState:
    """State: project -> thread_id -> ThreadSession."""
    # {project_key: {thread_id: {session_id, mode}}}
    sessions: dict[str, dict[str, ThreadSession]] = field(default_factory=dict)

    def get_session(self, project: str, thread_id: str) -> ThreadSession | None:
        return self.sessions.get(project, {}).get(thread_id)

    def ensure_session(self, project: str, thread_id: str) -> ThreadSession:
        if project not in self.sessions:
            self.sessions[project] = {}
        if thread_id not in self.sessions[project]:
            self.sessions[project][thread_id] = ThreadSession()
        return self.sessions[project][thread_id]

    def set_mode(self, project: str, thread_id: str, mode: str) -> None:
        session = self.ensure_session(project, thread_id)
        session.mode = mode


def save_state(state: UserState) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "sessions": {
            proj: {
                tid: {"session_id": s.session_id, "mode": s.mode, "backend": s.backend}
                for tid, s in threads.items()
            }
            for proj, threads in state.sessions.items()
        },
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_state() -> UserState:
    if not STATE_FILE.exists():
        return UserState()
    try:
        data = json.loads(STATE_FILE.read_text())
        state = UserState()
        for proj, threads in data.get("sessions", {}).items():
            state.sessions[proj] = {}
            for tid, v in threads.items():
                state.sessions[proj][tid] = ThreadSession(
                    session_id=v.get("session_id", ""),
                    mode=v.get("mode", "discuss"),
                    backend=v.get("backend", ""),
                )
        return state
    except Exception:
        logger.exception("Failed to load state")
        return UserState()
