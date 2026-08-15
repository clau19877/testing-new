#!/usr/bin/env bash
#
# Manual-captcha session helper.
#
# Brings up a viewable browser display so a human can solve Riot's hCaptcha
# while update_email.py automates everything else (login form, MFA via IMAP,
# email update). Works two ways:
#
#   1) LOCAL (simplest): run update_email.py on your own machine with a real
#      headed browser and just solve the captcha by hand. You do NOT need this
#      script locally.
#
#   2) REMOTE (cloud VM): this script starts Xvfb + x11vnc + noVNC so you can
#      open the browser in your web browser and solve the captcha remotely.
#
# Usage (remote VM):
#   ./start_manual_session.sh                # starts display + VNC + noVNC :6080
#   # then in another shell:
#   CAPTCHA_PROVIDER=manual python update_email.py --headed
#
# Open http://<vm-host>:6080/vnc.html  (you may need to port-forward 6080).
#
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-99}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
GEOMETRY="${GEOMETRY:-1440x900x24}"
NOVNC_DIR="${NOVNC_DIR:-/tmp/noVNC}"

export DISPLAY=":${DISPLAY_NUM}"

echo "[manual-session] display=:${DISPLAY_NUM} vnc=${VNC_PORT} novnc=${NOVNC_PORT}"

# 1) Xvfb
if ! pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null; then
  echo "[manual-session] starting Xvfb :${DISPLAY_NUM} (${GEOMETRY})"
  Xvfb ":${DISPLAY_NUM}" -screen 0 "${GEOMETRY}" -ac +extension GLX +render -noreset \
    >/tmp/xvfb_manual.log 2>&1 &
  sleep 2
else
  echo "[manual-session] Xvfb :${DISPLAY_NUM} already running"
fi

# 2) x11vnc bound to that display
if ! pgrep -f "x11vnc.*:${DISPLAY_NUM}" >/dev/null; then
  echo "[manual-session] starting x11vnc on :${VNC_PORT}"
  x11vnc -display ":${DISPLAY_NUM}" -rfbport "${VNC_PORT}" -forever -shared -nopw \
    -quiet -bg -o /tmp/x11vnc_manual.log
  sleep 1
else
  echo "[manual-session] x11vnc already running"
fi

# 3) noVNC (websockify) web client
if [ ! -f "${NOVNC_DIR}/vnc.html" ]; then
  echo "[manual-session] fetching noVNC → ${NOVNC_DIR}"
  git clone --depth 1 https://github.com/novnc/noVNC.git "${NOVNC_DIR}" >/dev/null 2>&1 || true
fi
if ! pgrep -f "websockify.*${NOVNC_PORT}" >/dev/null; then
  echo "[manual-session] starting noVNC on :${NOVNC_PORT}"
  websockify --web="${NOVNC_DIR}" "${NOVNC_PORT}" "localhost:${VNC_PORT}" \
    >/tmp/novnc_manual.log 2>&1 &
  sleep 1
else
  echo "[manual-session] noVNC already running"
fi

echo ""
echo "[manual-session] READY"
echo "  Open:  http://<this-vm-host>:${NOVNC_PORT}/vnc.html   (port-forward ${NOVNC_PORT} if needed)"
echo "  Then run in another shell:"
echo "     cd $(pwd) && source .venv/bin/activate && set -a && source .env && set +a && \\"
echo "     CAPTCHA_PROVIDER=manual DISPLAY=:${DISPLAY_NUM} python update_email.py --headed"
echo ""
echo "  The script auto-fills login, waits for you to solve the captcha in the"
echo "  browser, then finishes MFA + email update automatically."
