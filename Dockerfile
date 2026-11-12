# syntax=docker/dockerfile:1

# Thin Render wrapper around the official cobalt image.
#
# Upstream (ghcr.io/imputnet/cobalt) uses WORKDIR /app, runs as USER node,
# exposes port 9000, and starts with CMD [ "node", "src/cobalt" ]; it
# defines no ENTRYPOINT. This image keeps those conventions and only
# swaps the CMD for a boot script that first adapts Render's runtime
# environment ($PORT, $RENDER_EXTERNAL_URL, $API_KEYS_JSON) to cobalt's
# API_* variables, then execs cobalt's normal start command.
FROM ghcr.io/imputnet/cobalt:latest

# The boot script runs as the node user and must rewrite the key file on
# every start, so pre-create /keys.json as root and hand it to node.
USER root
RUN touch /keys.json && chown node:node /keys.json

RUN cat > /app/render-start.sh <<'EOF'
#!/bin/sh
# Render boot wrapper for cobalt.
#
# Render injects $PORT and $RENDER_EXTERNAL_URL at runtime, while cobalt
# is configured through its own API_* variables. This script writes the
# API key database from the $API_KEYS_JSON env var, translates the Render
# vars into cobalt's, turns on mandatory API key auth, and then hands
# over to cobalt's normal start command (upstream CMD: node src/cobalt).
set -e

printf '%s' "$API_KEYS_JSON" > /keys.json

export API_PORT="${PORT:-9000}"
export API_URL="${RENDER_EXTERNAL_URL%/}/"
export API_KEY_URL="file:///keys.json"
export API_AUTH_REQUIRED=1

exec node src/cobalt
EOF

RUN chmod +x /app/render-start.sh

USER node
WORKDIR /app

CMD [ "/bin/sh", "/app/render-start.sh" ]
