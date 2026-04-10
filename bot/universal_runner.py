"""Universal AI assistant runner with switchable backend (Claude / Qwen Code)."""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# ── Backend discovery ──────────────────────────────────────────────────────

BACKEND_CLAUDE = "claude"
BACKEND_QWEN = "qwen"
VALID_BACKENDS = {BACKEND_CLAUDE, BACKEND_QWEN}


def _find_binary(name: str) -> str:
    """Find CLI binary in PATH or common locations."""
    env_override = os.environ.get(f"{name.upper()}_PATH")
    if env_override:
        return env_override
    env_override = os.environ.get(f"{name.upper().replace('-', '_')}_PATH")
    if env_override:
        return env_override
    found = shutil.which(name)
    if found:
        return found
    home = os.path.expanduser("~")
    for candidate in [
        os.path.join(home, ".local", "bin", name),
        os.path.join(home, ".npm-global", "bin", name),
        os.path.join(home, f".{name}", "bin", name),
        f"/usr/local/bin/{name}",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return name


CLAUDE_BIN = _find_binary("claude")
QWEN_BIN = _find_binary("qwen")

# ── Events ─────────────────────────────────────────────────────────────────


@dataclass
class ToolUseEvent:
    """Assistant is using a tool."""
    tool: str
    input_summary: str


@dataclass
class TextDelta:
    """Chunk of text from assistant's response."""
    text: str


@dataclass
class FinalResult:
    """Assistant finished responding."""
    text: str
    session_id: str
    cost_usd: float = 0.0


@dataclass
class ErrorResult:
    """Something went wrong."""
    error: str


Event = ToolUseEvent | TextDelta | FinalResult | ErrorResult


# ── Tool input summarization ──────────────────────────────────────────────

TOOL_ICONS = {
    "Read": "📖",
    "Edit": "✏️",
    "Write": "📝",
    "Bash": "⚙️",
    "Glob": "🔍",
    "Grep": "🔍",
    "WebSearch": "🌐",
    "WebFetch": "🌐",
    "agent": "🤖",
    "skill": "🎯",
    "list_directory": "📁",
    "grep_search": "🔍",
    "glob": "🔍",
    "save_memory": "💾",
    "todo_write": "📋",
    "web_fetch": "🌐",
    "run_shell_command": "⚙️",
    "ask_user_question": "❓",
}


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Create a human-readable summary of a tool call."""
    match tool_name:
        case "Read" | "read_file":
            path = tool_input.get("file_path", tool_input.get("path", "?"))
            return path.split("/")[-1] if "/" in str(path) else str(path)
        case "Edit":
            path = tool_input.get("file_path", "?")
            fname = path.split("/")[-1] if "/" in str(path) else str(path)
            old = tool_input.get("old_string", "")
            lines = old.count("\n") + 1 if old else 0
            return f"{fname} ({lines} lines)" if lines else fname
        case "Write" | "write_file":
            path = tool_input.get("file_path", tool_input.get("path", "?"))
            return path.split("/")[-1] if "/" in str(path) else str(path)
        case "Bash" | "run_shell_command":
            cmd = tool_input.get("command", "?")
            return cmd[:80] + "..." if len(str(cmd)) > 80 else str(cmd)
        case "Glob" | "glob":
            return tool_input.get("pattern", "?")
        case "Grep" | "grep_search":
            pattern = tool_input.get("pattern", "?")
            path = tool_input.get("path", "")
            if path:
                return f'"{pattern}" in {path.split("/")[-1]}'
            return f'"{pattern}"'
        case "list_directory":
            return tool_input.get("path", "?").split("/")[-1] or "?"
        case "agent":
            return tool_input.get("description", tool_input.get("prompt", "?"))[:60]
        case _:
            return str(tool_input)[:60]


# ── Stream-JSON event parser (shared logic) ────────────────────────────────

def _parse_stream_json_line(line: str):
    """Parse a single stream-json line into an Event or None."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    ev_type = event.get("type", "")

    if ev_type == "assistant":
        msg = event.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                tool = block.get("name", "?")
                inp = block.get("input", {})
                icon = TOOL_ICONS.get(tool, "🔧")
                summary = _summarize_tool_input(tool, inp)
                return ToolUseEvent(tool=f"{icon} {tool}", input_summary=summary)
            elif block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    return TextDelta(text=text)
    elif ev_type == "result":
        result_text = event.get("result", "")
        sid = event.get("session_id", "")
        cost = event.get("total_cost_usd", event.get("usage", {}).get("total_cost_usd", 0.0))
        return FinalResult(text=result_text, session_id=sid, cost_usd=cost)

    return None


# ── UniversalRunner ────────────────────────────────────────────────────────

class UniversalRunner:
    """Manages a CLI subprocess with switchable backend (claude or qwen)."""

    SEND_FILE_INSTRUCTION = (
        "\n\nFILE SENDING: If the user asks to send, show, or share a file, "
        "use the tag <<SEND_FILE:relative/path/to/file>> in your response. "
        "The bot will automatically send that file via Mattermost. "
        "You can send multiple files by using multiple tags. "
        "The path must be relative to the project root."
    )

    # Short instruction telling AI to load project context file if available
    CONTEXT_FILE_INSTRUCTION = (
        "\n\nAt the start of your work, if QWEN.md or CLAUDE.md exists in the "
        "project root directory, read it with the Read/read_file tool to understand "
        "the project context, conventions, and instructions. Prefer QWEN.md over "
        "CLAUDE.md if both exist."
    )

    def __init__(self, backend: str = BACKEND_QWEN):
        self.backend = backend
        self._binary = CLAUDE_BIN if backend == BACKEND_CLAUDE else QWEN_BIN
        self._process: asyncio.subprocess.Process | None = None
        self.is_running = False

    def _build_args(
        self,
        accept_edits: bool,
        allowed_tools: list[str] | None,
        session_id: str | None,
        continue_session: bool,
    ) -> list[str]:
        """Build CLI arguments based on backend and mode."""
        args: list[str] = [self._binary]

        if self.backend == BACKEND_CLAUDE:
            args.extend(["--print", "--verbose", "--output-format", "stream-json"])
            if accept_edits:
                args.extend(["--dangerously-skip-permissions"])
                args.extend(["--append-system-prompt",
                    "You are in WORK mode. You can edit files. "
                    "Before making changes, briefly explain what you plan to do."
                    + self.SEND_FILE_INSTRUCTION
                    + self.CONTEXT_FILE_INSTRUCTION
                ])
            else:
                if allowed_tools:
                    args.extend(["--allowedTools", ",".join(allowed_tools)])
                args.extend(["--disallowedTools", "Edit,Write,Bash,NotebookEdit"])
                args.extend(["--append-system-prompt",
                    "You are in DISCUSS mode via a Mattermost bot. "
                    "You have read-only access (Read, Glob, Grep). "
                    "Do NOT try to edit files — it's forbidden in this mode. "
                    "Answer thoroughly and substantively — the user communicates by voice "
                    "and expects a full dialogue, not one-word answers. "
                    "Discuss ideas, suggest options, ask clarifying questions."
                    + self.SEND_FILE_INSTRUCTION
                    + self.CONTEXT_FILE_INSTRUCTION
                ])
            if continue_session and session_id:
                args.extend(["--resume", session_id])
            args.append("-")  # read from stdin

        else:
            # Qwen Code
            args.extend(["-p", "-o", "stream-json"])
            args.extend(["--yolo"])
            if accept_edits:
                args.extend(["--append-system-prompt",
                    "You are in WORK mode. You can edit files. "
                    "Before making changes, briefly explain what you plan to do. "
                    "If the user mentions a previous conversation with another AI assistant, "
                    "ask them to copy-paste the relevant messages from the Mattermost chat history "
                    "since you don't have access to previous conversations."
                    + self.SEND_FILE_INSTRUCTION
                    + self.CONTEXT_FILE_INSTRUCTION
                ])
            else:
                if allowed_tools:
                    args.extend(["--allowed-tools"] + allowed_tools)
                args.extend(["--exclude-tools", "Edit", "Write", "run_shell_command", "write_file"])
                args.extend(["--append-system-prompt",
                    "You are in DISCUSS mode via a Mattermost bot. "
                    "You have read-only access (read_file, list_directory, glob, grep_search). "
                    "Do NOT try to edit files or run shell commands — it's forbidden in this mode. "
                    "Answer thoroughly and substantively — the user communicates by voice "
                    "and expects a full dialogue, not one-word answers. "
                    "Discuss ideas, suggest options, ask clarifying questions. "
                    "If the user mentions a previous conversation with another AI assistant, "
                    "ask them to copy-paste the relevant messages from the Mattermost chat history "
                    "since you don't have access to previous conversations."
                    + self.SEND_FILE_INSTRUCTION
                    + self.CONTEXT_FILE_INSTRUCTION
                ])
            # Only continue session if it's a Qwen session_id (UUID format)
            if continue_session and session_id and len(session_id) == 36 and session_id.count("-") == 4:
                args.extend(["-c"])
            args.append("-")

        return args

    async def run(
        self,
        message: str,
        cwd: str,
        session_id: str | None = None,
        continue_session: bool = False,
        allowed_tools: list[str] | None = None,
        accept_edits: bool = False,
    ) -> AsyncIterator[Event]:
        """Run AI CLI and yield parsed events."""
        args = self._build_args(accept_edits, allowed_tools, session_id, continue_session)
        logger.info("Running %s: args=%s, cwd=%s", self.backend, args, cwd)

        self.is_running = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
            )
            self._process.stdin.write(message.encode("utf-8"))
            await self._process.stdin.drain()
            self._process.stdin.close()

            accumulated_text = ""
            result_session_id = session_id or ""

            async def read_stderr():
                stderr_text = ""
                async for line in self._process.stderr:
                    decoded = line.decode("utf-8", errors="replace")
                    stderr_text += decoded
                    logger.warning("%s stderr: %s", self.backend, decoded.rstrip())
                return stderr_text

            stderr_task = asyncio.create_task(read_stderr())

            async for line in self._process.stdout:
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                ev = _parse_stream_json_line(line)
                if ev is None:
                    logger.warning("Unparseable line: %s", line[:200])
                    continue

                if isinstance(ev, ToolUseEvent):
                    yield ev
                elif isinstance(ev, TextDelta):
                    accumulated_text += ev.text
                    yield ev
                elif isinstance(ev, FinalResult):
                    if ev.session_id:
                        result_session_id = ev.session_id
                    if not ev.text and accumulated_text:
                        ev = FinalResult(text=accumulated_text, session_id=result_session_id, cost_usd=ev.cost_usd)
                    yield ev
                    return

            await stderr_task

            logger.warning("%s finished without result event. Accumulated: %d chars",
                          self.backend, len(accumulated_text))
            yield FinalResult(text=accumulated_text, session_id=result_session_id)

        except Exception as e:
            logger.error(f"{self.backend} error: %s", e)
            yield ErrorResult(error=str(e))
        finally:
            self.is_running = False

    async def stop(self):
        """Terminate the running process."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self.is_running = False


def create_runner(backend: str = BACKEND_QWEN) -> UniversalRunner:
    """Factory for creating runners."""
    if backend not in VALID_BACKENDS:
        raise ValueError(f"Unknown backend: {backend}. Valid: {VALID_BACKENDS}")
    return UniversalRunner(backend=backend)
