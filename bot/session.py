"""User session and project management for Mattermost bot with backend support.

Work isolation: !go creates a feature branch, clones to ~/work/<project>-<uuid>/,
AI works in isolation, then commits + pushes + creates MR in Forgejo.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECTS_FILE = Path(__file__).parent.parent / "data" / "projects.json"
STATE_FILE = Path(__file__).parent.parent / "data" / "state.json"

# Directory for isolated work copies
WORK_DIR = Path(os.path.expanduser("~")) / "work"

# Forgejo configuration
FORGEJO_URL = "http://localhost:3001"
FORGEJO_TOKEN_FILE = Path(os.path.expanduser("~")) / ".tokens" / "forgejo.env"
FORGEJO_OWNER = "sc"

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


def _load_forgejo_token() -> str:
    """Load Forgejo API token from .env file."""
    if FORGEJO_TOKEN_FILE.exists():
        text = FORGEJO_TOKEN_FILE.read_text().strip()
        for line in text.splitlines():
            if line.startswith("FORGEJO_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


@dataclass
class ThreadSession:
    """An AI assistant session tied to a Mattermost thread.

    In work mode (!go), the session creates an isolated work directory:
    - work_dir: ~/work/<project_key>-<uuid>/  (isolated clone)
    - branch: feature/task-<uuid>              (new branch from forgejo/master)
    - mr_id: Forgejo pull request ID           (created after push)
    - project_key: which project this session belongs to

    summary: compressed context from !compress — injected as system prompt
             when a new session starts after compression.
    """
    session_id: str = ""
    mode: str = "discuss"
    backend: str = ""  # which backend created this session (claude/qwen)
    summary: str = ""  # compressed context from !compress

    # Work isolation fields (set when !go is used)
    work_dir: str = ""       # ~/work/<project_key>-<uuid>/
    branch: str = ""         # feature/task-<uuid>
    mr_id: str = ""          # Forgejo PR/MR number
    mr_url: str = ""         # Full URL to the MR
    project_key: str = ""    # which project this work session belongs to

    @property
    def is_working(self) -> bool:
        """True if this session has an active work directory."""
        return bool(self.work_dir) and os.path.isdir(self.work_dir)

    def generate_work_dir(self, project_key: str) -> str:
        """Generate a unique work directory path and branch name."""
        task_uuid = str(uuid.uuid4())[:8]
        self.work_dir = str(WORK_DIR / f"{project_key}-{task_uuid}")
        self.branch = f"feature/task-{task_uuid}"
        self.project_key = project_key
        return self.work_dir


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
                tid: {
                    "session_id": s.session_id,
                    "mode": s.mode,
                    "backend": s.backend,
                    "summary": s.summary,
                    "work_dir": s.work_dir,
                    "branch": s.branch,
                    "mr_id": s.mr_id,
                    "mr_url": s.mr_url,
                    "project_key": s.project_key,
                }
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
                    summary=v.get("summary", ""),
                    work_dir=v.get("work_dir", ""),
                    branch=v.get("branch", ""),
                    mr_id=str(v.get("mr_id", "")),
                    mr_url=v.get("mr_url", ""),
                    project_key=v.get("project_key", ""),
                )
        return state
    except Exception:
        logger.exception("Failed to load state")
        return UserState()
