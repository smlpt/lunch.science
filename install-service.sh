#!/usr/bin/env bash
#
# install-service.sh - deploy lunch.science as a headless systemd service.
#
# Run this ON THE VM, as root (or with sudo):
#     sudo bash install-service.sh
#
# It will:
#   1. create a dedicated non-root user "lunch"
#   2. copy this repo to /opt/lunch.science (owned by lunch)
#   3. build a venv and install dependencies + the Playwright Chromium browser
#   4. prompt for (or read) the vision-LLM API key into /home/lunch/.lunch_env
#   5. install + enable + start the systemd service
#
# After it finishes the site is up on http://<vm>:8080 and survives reboots
# and closing your terminal.
#
# Idempotent: safe to re-run after a `git pull` to redeploy.
set -euo pipefail

# --- configuration (edit these to taste) --------------------------------------
APP_USER="lunch"
APP_HOME="/home/${APP_USER}"
APP_DIR="/opt/lunch.science"
PORT="${LUNCH_PORT:-8080}"
# Where this script lives (the repo you're running it from).
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Pass --reconfigure-llm to re-prompt for the vision-LLM endpoint/key even if
# ~/.lunch_env already exists (e.g. to fix a wrong endpoint after first install).
RECONFIGURE_LLM=0
for arg in "$@"; do
  [[ "$arg" == "--reconfigure-llm" ]] && RECONFIGURE_LLM=1
done
# -----------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root:  sudo bash install-service.sh" >&2
  exit 1
fi

echo "==> [1/6] creating user '${APP_USER}' (if needed)"
if id "${APP_USER}" >/dev/null 2>&1; then
  echo "    user '${APP_USER}' already exists"
else
  useradd --system --create-home --home-dir "${APP_HOME}" \
          --shell /usr/sbin/nologin "${APP_USER}"
  echo "    created system user '${APP_USER}' (home ${APP_HOME})"
fi
mkdir -p "${APP_HOME}"
chown "${APP_USER}:${APP_USER}" "${APP_HOME}"

echo "==> [2/6] copying repo to ${APP_DIR}"
mkdir -p "${APP_DIR}"
# Copy the project (excluding venv, git, data, caches).
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'data' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude 'lunch.science.log' \
  "${SRC_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
mkdir -p "${APP_DIR}/data"
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
# Keep an existing data/menus.json if one is already there.
if [[ ! -f "${APP_DIR}/data/menus.json" ]]; then
  : # nothing to preserve; the app will scrape on first start
fi

echo "==> [3/6] building venv + installing dependencies"
cd "${APP_DIR}"
# Use the system python3 for the venv (the venv is owned by the app user).
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" --version 2>/dev/null || true
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
# Playwright needs its browser installed for the app user.
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" -m playwright install chromium

echo "==> [4/6] setting up the vision-LLM config (Kochwerk & Jakobs Speiselokal)"
KEY_FILE="${APP_HOME}/.lunch_env"
if [[ -f "${KEY_FILE}" && ${RECONFIGURE_LLM} -eq 0 ]]; then
  echo "    ${KEY_FILE} already exists - leaving it untouched."
  echo "    Current config (key masked):"
  grep -E '^LUNCH_(BASE_URL|MODEL)=' "${KEY_FILE}" | sed 's/^/      /' || true
  if grep -qE '^LUNCH_API_KEY=.+' "${KEY_FILE}"; then
    echo "      LUNCH_API_KEY=<set>"
  else
    echo "      LUNCH_API_KEY=<empty>"
  fi
  echo "    (To change it, edit that file, or 'rm' it and re-run this script.)"
else
  echo "    No ${KEY_FILE} found. Configure the vision-LLM endpoint here."
  echo "    (These are OpenAI-*compatible*; any of them left empty means the"
  echo "     built-in default is used. If the key is empty, Kochwerk & Jakobs"
  echo "     run 'offline' until you set it.)"
  echo
  read -r -p "    Vision LLM base URL   [https://api.openai.com/v1]: " LLM_BASE
  read -r -p "    Vision LLM model      [gpt-4o]:                    " LLM_MODEL
  read -r -s -p "    Vision LLM API key    (type, then Enter):        " API_KEY
  echo
  LLM_BASE="${LLM_BASE:-https://api.openai.com/v1}"
  LLM_MODEL="${LLM_MODEL:-gpt-4o}"
  if [[ -z "${API_KEY}" ]]; then
    echo "    no key provided - writing config with empty key (vision offline)"
  fi
  {
    echo "# lunch.science LLM config - only the 'lunch' user may read this (chmod 600)"
    echo "LUNCH_API_KEY=${API_KEY}"
    echo "LUNCH_BASE_URL=${LLM_BASE}"
    echo "LUNCH_MODEL=${LLM_MODEL}"
  } > "${KEY_FILE}"
  chown "${APP_USER}:${APP_USER}" "${KEY_FILE}"
  chmod 600 "${KEY_FILE}"
  echo "    wrote ${KEY_FILE} (chmod 600)"
  echo "      LUNCH_BASE_URL=${LLM_BASE}"
  echo "      LUNCH_MODEL=${LLM_MODEL}"
  if [[ -n "${API_KEY}" ]]; then
    echo "      LUNCH_API_KEY=<set>"
  else
    echo "      LUNCH_API_KEY=<empty>"
  fi
fi

echo "==> [5/6] installing systemd unit"
# Patch the port in the unit if it differs from the default.
sed "s/^Environment=LUNCH_PORT=.*/Environment=LUNCH_PORT=${PORT}/" \
  "${APP_DIR}/lunch.science.service" > /etc/systemd/system/lunch.science.service
chown root:root /etc/systemd/system/lunch.science.service
systemctl daemon-reload
systemctl enable lunch.science.service

echo "==> [6/6] starting the service"
systemctl restart lunch.science.service
sleep 2
systemctl --no-pager status lunch.science.service || true

echo
echo "============================================================"
echo " Done. lunch.science is running headlessly."
echo
echo "   site:        http://<vm-hostname>:${PORT}"
echo "   status:      systemctl status lunch.science"
echo "   logs:        journalctl -u lunch.science -f"
echo "   stop:        systemctl stop lunch.science"
echo "   start:       systemctl start lunch.science"
echo "   restart:     systemctl restart lunch.science"
echo
echo " After a 'git pull', redeploy with:  sudo bash install-service.sh"
echo "============================================================"
