#!/usr/bin/env python3
"""Rename Mattermost bot user — display name and optionally username."""
import os
import sys
from dotenv import load_dotenv
from mattermostdriver import Driver

load_dotenv()

raw_url = os.environ["MM_URL"]
token = os.environ["MM_BOT_TOKEN"]

if raw_url.startswith("http://"):
    scheme, host_port = "http", raw_url.replace("http://", "")
else:
    scheme, host_port = "https", raw_url.replace("https://", "")

if ":" in host_port:
    host, port = host_port.rsplit(":", 1)
    port = int(port)
else:
    host = host_port
    port = 443 if scheme == "https" else 8065

driver = Driver({
    "url": host, "token": token, "scheme": scheme, "port": port,
    "verify": scheme == "https", "timeout": 30,
})
driver.login()

me = driver.users.get_user("me")
old_username = me.get("username", "?")
old_display = me.get("nickname", me.get("display_name", "?"))

print(f"Current bot: username={old_username}, display='{old_display}'")

# Update display name (always safe)
patch_data = {
    "nickname": "AI Assistant",
    "first_name": "AI",
    "last_name": "Assistant",
}
# Also try username if available
new_username = "ai-assistant"
if sys.argv[1:]:
    new_username = sys.argv[1]

try:
    patch_data["username"] = new_username
except Exception:
    pass

driver.users.patch_user(me["id"], patch_data)

me_new = driver.users.get_user("me")
print(f"✅ Renamed: username={me_new.get('username')}, display='{me_new.get('nickname')}'")

