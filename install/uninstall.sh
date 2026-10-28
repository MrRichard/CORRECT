#!/usr/bin/env bash
#
# uninstall.sh — remove CORRECT from the system
#
# By default this removes only the service (unit file, firewall rule, stopped
# daemon) but LEAVES the 'correct' user, source, venv, config, and data in
# place — so it is safe to run before an update or to temporarily disable
# CORRECT.
#
# Use --purge to fully remove everything: user/group, /opt/correct, /mnt/shared,
# and all data. This returns the system to a pre-install state.
#
# Usage:
#   sudo ./uninstall.sh                 # remove service only (keep data/user)
#   sudo ./uninstall.sh --purge         # remove everything
#   sudo ./uninstall.sh --purge --keep-config   # purge but save config.yaml to /tmp
#   sudo ./uninstall.sh --dry-run
#
set -euo pipefail

PURGE=0
KEEP_CONFIG=0
DRY_RUN=0
PORT="11112"

die()  { echo "ERROR: $*" >&2; exit 1; }
run()  { echo ">> $*"; [ "$DRY_RUN" = "1" ] || "$@"; }
runb() { echo ">> $1"; [ "$DRY_RUN" = "1" ] || bash -c "$1"; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
  echo
  echo "Options: --purge | --keep-config | --port N | --dry-run | -h, --help"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift;;
    --keep-config) KEEP_CONFIG=1; shift;;
    --port) PORT="$2"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage;;
    *) die "unknown option: $1";;
  esac
done

[ "$(id -u)" = "0" ] || die "must run as root (use sudo)."

USER_NAME="correct"
APP="/opt/${USER_NAME}/app"
CONFIG="${APP}/config.yaml"
UNIT="/etc/systemd/system/correct.service"

# Try to read the listener port from config so the firewall rule matches.
if [ -f "$CONFIG" ]; then
  CFGPORT="$(grep -A3 '^dicom_listener:' "$CONFIG" 2>/dev/null | grep 'port:' | head -1 | sed 's/.*: *//; s/#.*//; s/ *$//' || true)"
  [ -n "$CFGPORT" ] && PORT="$CFGPORT"
fi

echo "==> [1/4] stopping and disabling 'correct' service"
run systemctl stop correct 2>/dev/null || true
run systemctl disable correct 2>/dev/null || true

echo "==> [2/4] removing systemd unit"
run rm -f "$UNIT"
run systemctl daemon-reload

echo "==> [3/4] removing firewall rule for port ${PORT}/tcp"
. /etc/os-release 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
  runb "ufw delete allow ${PORT}/tcp 2>/dev/null || true"
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
  runb "firewall-cmd --permanent --remove-port=${PORT}/tcp 2>/dev/null || true"
  runb "firewall-cmd --reload 2>/dev/null || true"
else
  echo "   no supported firewall tool found — skipping."
fi

if [ "$PURGE" = "1" ]; then
  echo "==> [4/4] --purge: removing user, source, venv, and data"
  if [ "$KEEP_CONFIG" = "1" ] && [ -f "$CONFIG" ]; then
    runb "cp '${CONFIG}' '/tmp/correct-config.yaml.bak'"
    echo "   saved config -> /tmp/correct-config.yaml.bak"
  fi
  run rm -rf "/opt/${USER_NAME}" /mnt/shared
  if id "$USER_NAME" >/dev/null 2>&1; then
    run userdel "$USER_NAME" 2>/dev/null || true
  fi
  if getent group "$USER_NAME" >/dev/null 2>&1; then
    run groupdel "$USER_NAME" 2>/dev/null || true
  fi
  echo "   removed /opt/${USER_NAME}, /mnt/shared, user '${USER_NAME}'"
else
  echo "==> [4/4] service-only removal (user, source, venv, config, and data kept)"
  echo "   To fully remove everything, re-run with --purge"
fi

echo
echo "================== UNINSTALL COMPLETE =================="
[ "$DRY_RUN" = "1" ] && echo "(dry-run: nothing was actually changed)"