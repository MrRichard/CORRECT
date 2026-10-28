#!/usr/bin/env bash
#
# update.sh — update an existing CORRECT install (bare-metal or VM)
#
# Pulls the latest code into /opt/correct/app/src, refreshes Python
# dependencies, and restarts the systemd service. The runtime config at
# /opt/correct/app/config.yaml lives OUTSIDE the source tree, so it is
# preserved automatically — no stash/restore dance needed.
#
# Works on any install created by install.sh or by the OVA Ansible playbook
# (both use the same layout).
#
# Usage:
#   sudo ./update.sh                  # pull master, restart
#   sudo ./update.sh --branch dev     # track a different branch
#   sudo ./update.sh --no-restart     # update code only, don't restart
#   sudo ./update.sh --dry-run
#
set -euo pipefail

BRANCH="master"
RESTART=1
DRY_RUN=0

die()  { echo "ERROR: $*" >&2; exit 1; }
run()  { echo ">> $*"; [ "$DRY_RUN" = "1" ] || "$@"; }
runb() { echo ">> $1"; [ "$DRY_RUN" = "1" ] || bash -c "$1"; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
  echo
  echo "Options: --branch NAME | --no-restart | --dry-run | -h, --help"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2;;
    --no-restart) RESTART=0; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage;;
    *) die "unknown option: $1";;
  esac
done

[ "$(id -u)" = "0" ] || die "must run as root (use sudo)."

USER_NAME="correct"
APP="/opt/${USER_NAME}/app"
SRC="${APP}/src"
VENV="${APP}/venv"
CONFIG="${APP}/config.yaml"

[ -d "${SRC}/.git" ] || die "CORRECT source not found at ${SRC} — is this a CORRECT install?"
[ -x "${VENV}/bin/python" ] || die "venv not found at ${VENV}."

echo "==> [1/4] stopping 'correct' service"
run systemctl stop correct 2>/dev/null || true

echo "==> [2/4] pulling latest code (branch: ${BRANCH})"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' fetch --quiet origin"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' checkout '${BRANCH}'"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' pull --ff-only origin '${BRANCH}'"

echo "==> [3/4] refreshing dependencies"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install --upgrade pip"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -r '${SRC}/requirements.txt'"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -e '${SRC}'"
echo "   (config at ${CONFIG} preserved — outside source tree)"

if [ "$RESTART" = "1" ]; then
  echo "==> [4/4] restarting 'correct' service"
  run systemctl start correct
  sleep 2
  if [ "$DRY_RUN" = "0" ]; then
    systemctl --no-pager --lines=15 status correct || true
    echo
    echo "--- new HEAD ---"
    runuser -u "$USER_NAME" -- git -C "$SRC" log --oneline -1
  fi
else
  echo "==> [4/4] --no-restart given; service is stopped. Start with: systemctl start correct"
fi
echo "===================== UPDATE COMPLETE ====================="