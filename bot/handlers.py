"""Mattermost message handlers with thread support and switchable AI backend."""

import asyncio
import logging
import os
import re

from . import stt
from .universal_runner import (
    UniversalRunner, ToolUseEvent, FinalResult, ErrorResult,
    create_runner, BACKEND_CLAUDE, BACKEND_QWEN, VALID_BACKENDS,
)
from .session import (
    PROJECTS, UserState, load_state, save_state, project_for_channel,
    reload_projects, PROJECTS_FILE,
)

logger = logging.getLogger(__name__)

# Default backend from environment variable
DEFAULT_BACKEND = os.environ.get("AI_BACKEND", BACKEND_QWEN).lower()
if DEFAULT_BACKEND not in VALID_BACKENDS:
    logger.warning("Invalid AI_BACKEND '%s', defaulting to qwen", DEFAULT_BACKEND)
    DEFAULT_BACKEND = BACKEND_QWEN

# Tools for discuss mode — backend-agnostic names mapped per backend
DISCUSS_TOOLS_CLAUDE = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
DISCUSS_TOOLS_QWEN = ["read_file", "list_directory", "glob", "grep_search", "web_fetch"]

AUDIO_MIMES = {
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/mp4",
    "audio/webm", "audio/x-m4a", "audio/flac",
}

IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb",
    ".java", ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".sh", ".bash",
    ".zsh", ".fish", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".json", ".xml", ".html", ".css", ".scss", ".sql", ".r", ".lua",
    ".pl", ".pm", ".php", ".ex", ".exs", ".erl", ".hs", ".ml", ".vim",
    ".dockerfile", ".makefile", ".cmake", ".gradle", ".env", ".gitignore",
    ".editorconfig", ".csv", ".tsv", ".log", ".diff", ".patch",
}

MAX_INLINE_SIZE = 100_000

# Backend display names for status messages
BACKEND_DISPLAY = {
    BACKEND_CLAUDE: "Claude",
    BACKEND_QWEN: "Qwen",
}


class MessageHandler:
    """Handles Mattermost messages and routes them to AI backend."""

    def __init__(self, driver, owner_user_id: str):
        self.driver = driver
        self.owner_user_id = owner_user_id
        self._state: UserState | None = None
        self._runners: dict[str, UniversalRunner] = {}  # thread_id -> runner
        self._channel_map: dict[str, str] = {}

    def get_state(self) -> UserState:
        if self._state is None:
            self._state = load_state()
        return self._state

    def _save(self):
        save_state(self.get_state())

    def _get_discuss_tools(self) -> list[str] | None:
        """Get discuss tools for the current backend."""
        if DEFAULT_BACKEND == BACKEND_CLAUDE:
            return DISCUSS_TOOLS_CLAUDE
        return DISCUSS_TOOLS_QWEN

    def _get_runner(self, thread_id: str) -> UniversalRunner:
        """Get or create a UniversalRunner for a thread."""
        if thread_id not in self._runners:
            self._runners[thread_id] = create_runner(DEFAULT_BACKEND)
        return self._runners[thread_id]

    def _get_channel_name(self, channel_id: str) -> str | None:
        if channel_id not in self._channel_map:
            try:
                ch = self.driver.channels.get_channel(channel_id)
                self._channel_map[channel_id] = ch.get("name", "")
            except Exception:
                return None
        return self._channel_map.get(channel_id)

    async def handle_post(self, post: dict) -> None:
        """Handle an incoming post from Mattermost WebSocket."""
        user_id = post.get("user_id", "")
        if user_id != self.owner_user_id:
            return

        channel_id = post.get("channel_id", "")
        message = post.get("message", "").strip()
        file_ids = post.get("file_ids") or []
        post_id = post.get("id", "")

        root_id = post.get("root_id", "") or post_id

        channel_name = self._get_channel_name(channel_id)
        if not channel_name:
            return
        project_key = project_for_channel(channel_name)
        if not project_key:
            return

        state = self.get_state()
        state.ensure_session(project_key, root_id)
        self._save()

        if message.startswith("!"):
            await self._handle_command(message, channel_id, root_id, project_key)
            return

        if file_ids:
            await self._handle_files(file_ids, channel_id, root_id, project_key, message)
            return

        if message:
            await self._process_message(message, channel_id, root_id, project_key)

    async def _handle_command(self, message: str, channel_id: str, thread_id: str, project_key: str) -> None:
        cmd = message.split()[0].lower()
        state = self.get_state()
        project = PROJECTS.get(project_key, {})
        backend_name = BACKEND_DISPLAY.get(DEFAULT_BACKEND, DEFAULT_BACKEND.title())

        if cmd == "!go":
            state.set_mode(project_key, thread_id, "work")
            self._save()
            self._post(channel_id, f"⚡ Mode: **work**\n{backend_name} can edit files.\n`!discuss` to switch back.", thread_id)

        elif cmd == "!discuss":
            state.set_mode(project_key, thread_id, "discuss")
            self._save()
            self._post(channel_id, f"💬 Mode: **discuss** (read-only) — {backend_name}", thread_id)

        elif cmd == "!new":
            session = state.ensure_session(project_key, thread_id)
            session.session_id = ""
            session.backend = ""  # Clear backend so any backend can be used
            self._save()
            backend_name = BACKEND_DISPLAY.get(DEFAULT_BACKEND, DEFAULT_BACKEND.title())
            self._post(channel_id,
                f"🆕 Новая сессия в {project.get('name', project_key)}\n"
                f"Backend: {backend_name}",
                thread_id,
            )

        elif cmd == "!stop":
            runner = self._runners.get(thread_id)
            if runner and runner.is_running:
                await runner.stop()
                self._post(channel_id, "🛑 Stopped", thread_id)
            else:
                self._post(channel_id, f"{backend_name} is not running", thread_id)

        elif cmd == "!status":
            session = state.get_session(project_key, thread_id)
            mode = "⚡ work" if session and session.mode == "work" else "💬 discuss"
            runner = self._runners.get(thread_id)
            running = "▶️ yes" if runner and runner.is_running else "⏹ no"
            self._post(channel_id,
                f"**Project:** {project.get('name', project_key)}\n"
                f"📂 `{project.get('path', '?')}`\n"
                f"**Backend:** {backend_name}\n"
                f"**Mode:** {mode}\n"
                f"**{backend_name}:** {running}",
                thread_id,
            )

        elif cmd == "!reload":
            projects = reload_projects()
            self._channel_map.clear()
            self._post(channel_id,
                f"🔄 Reloaded {len(projects)} projects from `{PROJECTS_FILE.name}`",
                thread_id,
            )

        elif cmd == "!help":
            self._post(channel_id,
                "**Commands:**\n"
                "- `!go` — work mode (allow edits)\n"
                "- `!discuss` — discuss mode (read-only)\n"
                "- `!new` — new session in this thread\n"
                "- `!stop` — stop AI assistant\n"
                "- `!status` — show current state\n"
                "- `!reload` — reload projects from config\n\n"
                f"**Backend:** {backend_name} (set via AI_BACKEND env var)\n"
                "**Threads:** Each thread = separate session.\n"
                "Start a new message for a new topic.",
                thread_id,
            )

    async def _handle_files(self, file_ids: list[str], channel_id: str,
                            thread_id: str, project_key: str, caption: str) -> None:
        groq_key = os.environ.get("GROQ_API_KEY")
        tmp_dir = os.path.join(os.path.dirname(__file__), "..", "data", "tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        for file_id in file_ids:
            try:
                file_info = self.driver.files.get_file_metadata(file_id)
                mime = file_info.get("mime_type", "")
                filename = file_info.get("name", f"file_{file_id}")

                file_resp = self.driver.files.get_file(file_id)
                local_path = os.path.join(tmp_dir, filename)
                with open(local_path, "wb") as f:
                    f.write(file_resp.content)

                if mime in AUDIO_MIMES and groq_key:
                    mp3_path = await stt.convert_to_mp3(local_path)
                    stt_lang = os.environ.get("STT_LANGUAGE", "ru")
                    text = await stt.transcribe(groq_key, mp3_path, language=stt_lang)
                    for p in {local_path, mp3_path}:
                        _safe_remove(p)
                    if text:
                        self._post(channel_id, f"🎤 {text}", thread_id)
                        await self._process_message(text, channel_id, thread_id, project_key)
                    else:
                        self._post(channel_id, "Could not transcribe audio.", thread_id)

                elif mime in IMAGE_MIMES:
                    prompt = f"Image saved at: {os.path.abspath(local_path)}\nUse the Read tool to view this image."
                    if caption:
                        prompt += f"\n\n{caption}"
                    else:
                        prompt += "\n\nDescribe and analyze this image."
                    self._post(channel_id, f"📷 {filename}", thread_id)
                    await self._process_message(prompt, channel_id, thread_id, project_key)

                else:
                    text_content = _try_read_text(local_path)
                    if text_content is not None:
                        prompt = f"File: {filename}\n```\n{text_content}\n```"
                        if caption:
                            prompt += f"\n\n{caption}"
                        else:
                            prompt += "\n\nAnalyze this file."
                        _safe_remove(local_path)
                    else:
                        prompt = f"File saved at: {os.path.abspath(local_path)}\nFilename: {filename}"
                        if caption:
                            prompt += f"\n\n{caption}"
                        else:
                            prompt += "\n\nAnalyze this file."
                    self._post(channel_id, f"📎 {filename}", thread_id)
                    await self._process_message(prompt, channel_id, thread_id, project_key)

            except Exception:
                logger.exception("File processing error for %s", file_id)
                self._post(channel_id, f"❌ Error processing file {file_id}", thread_id)

    async def _process_message(self, text: str, channel_id: str,
                                thread_id: str, project_key: str) -> None:
        """Send message to AI backend and stream results."""
        project = PROJECTS.get(project_key)
        if not project:
            self._post(channel_id, "No project mapped to this channel.", thread_id)
            return

        runner = self._get_runner(thread_id)
        backend_name = BACKEND_DISPLAY.get(DEFAULT_BACKEND, DEFAULT_BACKEND.title())

        if runner.is_running:
            self._post(channel_id, f"⏳ {backend_name} is still running. `!stop` to interrupt.", thread_id)
            return

        state = self.get_state()
        session = state.ensure_session(project_key, thread_id)
        self._save()

        accept_edits = session.mode == "work"
        allowed_tools = self._get_discuss_tools() if not accept_edits else None

        # Check if backend changed since last session
        backend_mismatch = session.backend and session.backend != DEFAULT_BACKEND
        if backend_mismatch:
            old_backend_name = BACKEND_DISPLAY.get(session.backend, session.backend.title())
            new_backend_name = BACKEND_DISPLAY.get(DEFAULT_BACKEND, DEFAULT_BACKEND.title())
            self._post(channel_id,
                f"⚠️ Этот диалог был начат с **{old_backend_name}**.\n"
                f"Сейчас бот работает на **{new_backend_name}**.\n\n"
                f"Чтобы продолжить — переключи бот обратно на {old_backend_name} "
                f"(измени `AI_BACKEND` в `.env` и перезапусти).\n"
                f"Или используй `!new` чтобы начать новую сессию с {new_backend_name}.",
                thread_id,
            )
            return  # Don't process the message

        # Set current backend on session (first time or matching backend)
        session.backend = DEFAULT_BACKEND
        self._save()
        has_session = bool(session.session_id)

        # Prepend language instruction
        text = (
            "IMPORTANT: Always respond in Russian language (русский язык). "
            "Even if the user writes in English, answer in Russian.\n\n"
            + text
        )

        # Post initial status
        status_post = self._post(channel_id, "🤖 Thinking...", thread_id)
        status_post_id = status_post.get("id", "")

        tool_lines = []
        final_text = ""
        last_update_len = 0

        def update_status():
            nonlocal last_update_len
            content = "\n".join(tool_lines) if tool_lines else "🤖 Thinking..."
            if len(content) != last_update_len and status_post_id:
                last_update_len = len(content)
                try:
                    self.driver.posts.patch_post(status_post_id, {"message": content})
                except Exception:
                    pass

        try:
            async for event in runner.run(
                message=text,
                cwd=project["path"],
                session_id=session.session_id if has_session else None,
                continue_session=has_session,
                allowed_tools=allowed_tools,
                accept_edits=accept_edits,
            ):
                if isinstance(event, ToolUseEvent):
                    tool_lines.append(f"{event.tool}: {event.input_summary}")
                    update_status()

                elif isinstance(event, FinalResult):
                    final_text = event.text
                    session.session_id = event.session_id
                    self._save()

                elif isinstance(event, ErrorResult):
                    final_text = f"❌ {event.error}"

            if tool_lines:
                try:
                    self.driver.posts.patch_post(status_post_id, {"message": "\n".join(tool_lines)})
                except Exception:
                    pass
            elif status_post_id:
                try:
                    self.driver.posts.delete_post(status_post_id)
                except Exception:
                    pass

            if final_text:
                file_paths = _extract_file_paths(final_text)
                clean_text = _remove_file_tags(final_text)

                if clean_text.strip():
                    for chunk in _split_message(clean_text, 16000):
                        self._post(channel_id, chunk, thread_id)

                for rel_path in file_paths:
                    self._send_file(channel_id, project["path"], rel_path, thread_id)

        except Exception:
            logger.exception("AI backend processing error")
            backend_name = BACKEND_DISPLAY.get(backend, backend.title())
            self._post(channel_id, f"❌ {backend_name} processing failed. Check bot logs.", thread_id)

    def _post(self, channel_id: str, message: str, thread_id: str = "") -> dict:
        """Create a post, optionally in a thread."""
        payload = {
            "channel_id": channel_id,
            "message": message,
        }
        if thread_id:
            payload["root_id"] = thread_id
        return self.driver.posts.create_post(payload)

    def _send_file(self, channel_id: str, project_path: str, rel_path: str, thread_id: str = "") -> None:
        full_path = os.path.join(project_path, rel_path)
        if not os.path.isfile(full_path):
            self._post(channel_id, f"⚠️ File not found: {rel_path}", thread_id)
            return

        real_project = os.path.realpath(project_path)
        real_file = os.path.realpath(full_path)
        if not real_file.startswith(real_project):
            self._post(channel_id, f"⚠️ Access denied: {rel_path}", thread_id)
            return

        try:
            upload = self.driver.files.upload_file(
                channel_id=channel_id,
                files={"files": (os.path.basename(full_path), open(full_path, "rb"))},
            )
            file_id = upload["file_infos"][0]["id"]
            payload = {
                "channel_id": channel_id,
                "message": "",
                "file_ids": [file_id],
            }
            if thread_id:
                payload["root_id"] = thread_id
            self.driver.posts.create_post(payload)
        except Exception as e:
            logger.error("Failed to send file %s: %s", full_path, e)
            self._post(channel_id, f"⚠️ Error sending: {rel_path}", thread_id)


_FILE_TAG_RE = re.compile(r"<<SEND_FILE:(.+?)>>")

def _extract_file_paths(text: str) -> list[str]:
    return _FILE_TAG_RE.findall(text)

def _remove_file_tags(text: str) -> str:
    return _FILE_TAG_RE.sub("", text).strip()

def _try_read_text(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    basename = os.path.basename(path).lower()
    is_likely_text = (
        ext in TEXT_EXTENSIONS
        or basename in ("makefile", "dockerfile", "vagrantfile", "rakefile", "gemfile")
    )
    if not is_likely_text:
        return None
    try:
        size = os.path.getsize(path)
        if size > MAX_INLINE_SIZE:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None

def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass

def _split_message(text: str, max_len: int = 16000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
