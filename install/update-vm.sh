#!/usr/bin/env bash
#
# update-vm.sh — update CORRECT on a deployed OVA appliance (Debian Trixie)
#
# Modeled on the correct-ova-build Ansible layout. Self-contained — safe to
# copy this single file onto a VM that has no other install/ scripts.
#
# Layout (created by the OVA Ansible playbook):
#   /opt/correct/app/src          CORRECT git clone (branch: master)
#   /opt/correct/app/venv         Python virtualenv
#   /opt/correct/app/config.yaml  site-specific runtime config (single source of truth)
#   /opt/correct/{working,logs}   data dirs
#   /mnt/shared                   reports + PID mapping
#   ufw: allow 22/tcp, 11112/tcp
#   systemd: correct.service
#
# What this does:
#   1. Stops the 'correct' service.
#   2. Backs up config.yaml (safety net — it's outside src/, so pulls won't
#      touch it, but we back it up anyway).
#   3. Pulls the latest master into /opt/correct/app/src.
#   4. Reinstalls requirements.txt AND runs 'pip install -e .' — this handles
#      the transition from older OVA images (built when master had no
#      pyproject.toml) to current master (pyproject.toml + rt-utils shim).
#   5. Ensures the ufw rule for port 11112 still exists.
#   6. Restarts the service and runs the post-build verification checks.
#
# Usage:
#   sudo ./update-vm.sh                # default: pull master
#   sudo ./update-vm.sh --branch dev
#   sudo ./update-vm.sh --dry-run
#
set -euo pipefail

BRANCH="master"
DRY_RUN=0

die()  { echo "ERROR: $*" >&2; exit 1; }
run()  { echo ">> $*"; [ "$DRY_RUN" = "1" ] || "$@"; }
runb() { echo ">> $1"; [ "$DRY_RUN" = "1" ] || bash -c "$1"; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
  echo
  echo "Options: --branch NAME | --dry-run | -h, --help"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2;;
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
STAMP="$(date +%Y%m%d-%H%M%S)"

[ -d "${SRC}/.git" ] || die "CORRECT source not found at ${SRC} — is this a CORRECT OVA VM?"
[ -f "$CONFIG" ]    || die "config not found at ${CONFIG}."

echo "==> [1/6] stopping 'correct' service"
run systemctl stop correct 2>/dev/null || true

echo "==> [2/6] backing up config (safety net)"
runb "cp -p '${CONFIG}' '${CONFIG}.bak-${STAMP}'"
runb "ls -1 '${CONFIG}.bak-'* 2>/dev/null | tail -n 5" || true

echo "==> [3/6] pulling latest code (branch: ${BRANCH})"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' fetch --quiet origin"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' checkout '${BRANCH}'"
runb "runuser -u ${USER_NAME} -- git -C '${SRC}' pull --ff-only origin '${BRANCH}'"

echo "==> [4/6] refreshing dependencies (requirements.txt + editable install)"
# OVA-built venvs are root-owned (Ansible provisioned as root), which blocks pip
# writes as the service user. Normalize ownership; no-op on fresh installs.
runb "chown -R ${USER_NAME}:${USER_NAME} '${VENV}' '${SRC}'"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install --upgrade pip 2>/dev/null || echo \"   (pip upgrade skipped — using existing)\""
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -r '${SRC}/requirements.txt'"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -e '${SRC}'"

echo "==> [5/6] verifying ufw allows the DICOM listener port"
PORT="$(grep -A3 '^dicom_listener:' "$CONFIG" 2>/dev/null | grep 'port:' | head -1 | sed 's/.*: *//; s/#.*//; s/ *$//' || true)"
PORT="${PORT:-11112}"
if command -v ufw >/dev/null 2>&1; then
  runb "ufw allow ${PORT}/tcp 2>/dev/null || true"
else
  echo "   ufw not installed — skipping firewall check."
fi

echo "==> [6/6] restarting service and running verification"
run systemctl start correct
if [ "$DRY_RUN" = "0" ]; then
  sleep 2
  echo
  echo "--- service status ---"
  systemctl --no-pager --lines=12 status correct || true
  echo
  echo "--- listener socket ---"
  ss -tlnp 2>/dev/null | grep ":${PORT}" || echo "   (port ${PORT} not yet bound — check journalctl -u correct)"
  echo
  echo "--- firewall ---"
  ufw status 2>/dev/null || true
  echo
  echo "--- new HEAD ---"
  runuser -u "$USER_NAME" -- git -C "$SRC" log --oneline -1
  echo
  echo "--- recent logs ---"
  journalctl -u correct --no-pager -n 15 || true
fi
echo
echo "=============== VM UPDATE COMPLETE ==============="
echo "  Config backups:  ${CONFIG}.bak-*"
echo "  If something is wrong, restore with:"
echo "    sudo cp ${CONFIG}.bak-${STAMP} ${CONFIG} && sudo systemctl restart correct"
echo "==================================================="
[ "$DRY_RUN" = "1" ] && echo "(dry-run: nothing was actually changed)"