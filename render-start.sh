#!/bin/sh
# Render boot wrapper for the yt-dlp engine.
#
# Converts $YT_COOKIES_JSON (cobalt format: {"youtube": ["name=value; ..."]},
# set in the Render dashboard only, never committed) into a Netscape-format
# /cookies.txt that yt-dlp can read, then starts the API server, which maps
# Render's $PORT / $RENDER_EXTERNAL_URL itself. With the env var unset or
# empty the engine simply runs cookieless.
set -e

python3 - <<'PYEOF'
import json
import os

raw = os.environ.get("YT_COOKIES_JSON", "").strip()
lines = ["# Netscape HTTP Cookie File"]
count = 0
if raw:
    try:
        data = json.loads(raw)
        pairs = []
        if isinstance(data, dict):
            for entries in data.values():
                items = entries if isinstance(entries, list) else [entries]
                for entry in items:
                    if isinstance(entry, str):
                        pairs.extend(
                            p.strip() for p in entry.split(";") if p.strip()
                        )
        seen = set()
        for pair in pairs:
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name, value = name.strip(), value.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            for domain in (".youtube.com", ".google.com"):
                lines.append(
                    "%s\tTRUE\t/\tTRUE\t2000000000\t%s\t%s" % (domain, name, value)
                )
                count += 1
    except Exception as exc:  # never print values, only the failure kind
        print("cookie conversion failed: %s" % type(exc).__name__)
with open("/cookies.txt", "w") as fh:
    fh.write("\n".join(lines) + "\n")
os.chmod("/cookies.txt", 0o600)
print("cookies.txt written: %d entries" % count)
PYEOF

exec python3 /app/server.py
