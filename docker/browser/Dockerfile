# Kageha sandboxed Chromium with CDP + noVNC observer (OpenClaw-style).
# Build: docker build -t kageha-browser:local docker/browser
# Run via KAGEHA_BROWSER_MODE=docker (auto) or:
#   docker run --rm -p 9222:9222 -p 6080:6080 -e NOVNC_PASSWORD=secret kageha-browser:local

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    CHROME_REMOTE_DEBUGGING_PORT=9222 \
    DISPLAY=:99 \
    NOVNC_PORT=6080

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fonts-liberation \
    ca-certificates \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9222 6080
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
  CMD curl -fsS http://127.0.0.1:9222/json/version || exit 1

ENTRYPOINT ["/entrypoint.sh"]
