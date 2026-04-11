"""Mattermost AI Bot — entry point with switchable backend (Claude / Qwen Code)."""

import asyncio
import json
import logging
import os
import sys

import websockets
from dotenv import load_dotenv
from mattermostdriver import Driver

from .handlers import MessageHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def create_driver() -> Driver:
    """Create and configure Mattermost driver."""
    raw_url = os.environ["MM_URL"]
    token = os.environ["MM_BOT_TOKEN"]

    if raw_url.startswith("http://"):
        scheme = "http"
        host_port = raw_url.replace("http://", "")
    else:
        scheme = "https"
        host_port = raw_url.replace("https://", "")

    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 443 if scheme == "https" else 8065

    return Driver({
        "url": host,
        "token": token,
        "scheme": scheme,
        "port": port,
        "verify": scheme == "https",
        "timeout": 120,
    })


def get_ws_url() -> str:
    """Build WebSocket URL from MM_URL."""
    raw_url = os.environ["MM_URL"]
    if raw_url.startswith("https://"):
        return raw_url.replace("https://", "wss://") + "/api/v4/websocket"
    return raw_url.replace("http://", "ws://") + "/api/v4/websocket"


async def websocket_listener(handler: MessageHandler, bot_user_id: str):
    """Connect to Mattermost WebSocket and listen for events."""
    ws_url = get_ws_url()
    token = os.environ["MM_BOT_TOKEN"]

    while True:
        try:
            logger.info("Connecting to WebSocket at %s", ws_url)
            async with websockets.connect(ws_url) as ws:
                # Authenticate
                auth = json.dumps({
                    "seq": 1,
                    "action": "authentication_challenge",
                    "data": {"token": token},
                })
                await ws.send(auth)

                logger.info("WebSocket connected and authenticated")

                async for message in ws:
                    try:
                        event = json.loads(message)
                        if event.get("event") != "posted":
                            continue

                        data = event.get("data", {})
                        post_str = data.get("post", "")
                        post = json.loads(post_str) if isinstance(post_str, str) else post_str

                        if post.get("user_id") == bot_user_id:
                            continue

                        # Handle in background so we don't block the listener
                        asyncio.create_task(handler.handle_post(post))

                    except Exception:
                        logger.exception("Error processing event")

        except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
            logger.warning("WebSocket disconnected: %s. Reconnecting in 5s...", e)
            await asyncio.sleep(5)
        except Exception:
            logger.exception("WebSocket error. Reconnecting in 10s...")
            await asyncio.sleep(10)


async def async_main():
    load_dotenv()

    required_vars = ["MM_URL", "MM_BOT_TOKEN", "MM_OWNER_USERNAME"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        logger.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    driver = create_driver()
    driver.login()
    logger.info("Logged in to Mattermost at %s", os.environ["MM_URL"])

    owner_username = os.environ["MM_OWNER_USERNAME"]
    owner = driver.users.get_user_by_username(owner_username)
    owner_user_id = owner["id"]

    me = driver.users.get_user("me")
    bot_user_id = me["id"]
    logger.info("Owner: %s (%s), Bot: %s (%s)", owner_username, owner_user_id, me["username"], bot_user_id)

    handler = MessageHandler(driver, owner_user_id)

    await websocket_listener(handler, bot_user_id)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
