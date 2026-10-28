#!/usr/bin/env bash
#
# install.sh — CORRECT DICOM RT Structure Set Processor
# Unified installer for Debian/Ubuntu and RHEL/Rocky/Fedora.
#
# Installs CORRECT as a systemd service under a dedicated 'correct' user,
# using the same on-disk layout as the OVA appliances so that update.sh /
# update-vm.sh work on both bare-metal and VM deployments:
#
#   /opt/correct/app/src      CORRECT source (git clone or --source copy)
#   /opt/correct/app/venv     Python virtualenv
#   /opt/correct/app/config.yaml   runtime config (outside src/ — survives pulls)
#   /opt/correct/working      in-progress study storage + quarantine
#   /opt/correct/logs         application + transaction logs
#   /mnt/shared               reports + anonymized PID mapping
#   /etc/systemd/system/correct.service
#
# Usage:
#   sudo ./install.sh                         # defaults: clone master, port 11112
#   sudo ./install.sh --branch main           # deploy a specific branch
#   sudo ./install.sh --source /home/me/CORRECT  # copy a local working copy into src/
#   sudo ./install.sh --config /path/cfg.yaml # install a pre-made config
#   sudo ./install.sh --dry-run               # print every step, change nothing
#   sudo ./install.sh --help
#
set -euo pipefail

# ── defaults ────────────────────────────────────────────────────────────────
REPO="https://github.com/MrRichard/CORRECT.git"
BRANCH="master"
PORT="11112"
USER_NAME="correct"
GROUP_NAME="correct"
CUSTOM_CONFIG=""
SOURCE=""
DRY_RUN=0

# ── helpers ─────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
run()  { echo ">> $*"; [ "$DRY_RUN" = "1" ] || "$@"; }
runb() { echo ">> $1"; [ "$DRY_RUN" = "1" ] || bash -c "$1"; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
  cat <<EOF

Options:
  --branch NAME      git branch/tag to deploy (default: master; ignored with --source)
  --repo URL         git repository URL (default: $REPO)
  --source PATH      copy a local source dir into src/ instead of cloning (root reads it,
                     so the 'correct' user never needs access to your home dir)
  --config PATH      copy this file to config.yaml instead of the template
  --port N           DICOM listener port + firewall rule (default: 11112)
  --user NAME        service account name (default: correct)
  --dry-run          print every command without executing
  -h, --help         show this help
EOF
  exit 0
}

# ── parse args ──────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2;;
    --repo)   REPO="$2"; shift 2;;
    --source) SOURCE="$2"; shift 2;;
    --config) CUSTOM_CONFIG="$2"; shift 2;;
    --port)   PORT="$2"; shift 2;;
    --user)   USER_NAME="$2"; GROUP_NAME="$2"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage;;
    *) die "unknown option: $1 (try --help)";;
  esac
done

[ "$(id -u)" = "0" ] || die "must run as root (use sudo)."

APP="/opt/${USER_NAME}/app"
SRC="${APP}/src"
VENV="${APP}/venv"
CONFIG="${APP}/config.yaml"
WORKING="/opt/${USER_NAME}/working"
LOGS="/opt/${USER_NAME}/logs"
SHARED="/mnt/shared"
UNIT="/etc/systemd/system/correct.service"

# ── detect distro family ────────────────────────────────────────────────────
. /etc/os-release 2>/dev/null || die "cannot read /etc/os-release"
FAMILY=""
case "${ID:-}" in
  debian|ubuntu) FAMILY="debian";;
  fedora)        FAMILY="rhel"; SUB="fedora";;
  rhel|rocky|almalinux|centos|ol) FAMILY="rhel"; SUB="el";;
  *)
    case "${ID_LIKE:-}" in
      *debian*) FAMILY="debian";;
      *rhel*|*fedora*) FAMILY="rhel"; SUB="el";;
      *) die "unsupported distro: ID=${ID:-unknown} (edit install.sh to add it)";;
    esac;;
esac
echo "Detected: ${PRETTY_NAME:-$ID}  (family=$FAMILY)"

# ── 1. system packages + Python ─────────────────────────────────────────────
echo "==> [1/9] installing system packages and Python"
PYBIN=""
if [ "$FAMILY" = "debian" ]; then
  runb "apt-get update -qq"
  runb "apt-get install -y git ca-certificates"
  # Prefer python3.11; fall back to the distro default if 3.11 isn't packaged
  # (e.g. Debian Trixie ships 3.13 as default — the pinned stack works on 3.11-3.13).
  if runb "apt-get install -y python3.11 python3.11-venv python3.11-pip 2>/dev/null"; then
    PYBIN="python3.11"
  else
    echo "WARN: python3.11 not packaged on this distro; falling back to system python3."
    runb "apt-get install -y python3-venv python3-pip"
    PYBIN="python3"
  fi
else  # rhel family
  runb "dnf install -y git"
  runb "dnf install -y python3.11"
  if [ "${SUB:-}" = "fedora" ]; then
    # Fedora has no python3.11-pip; use python3-pip (venv gets pip via ensurepip).
    runb "dnf install -y python3-pip 2>/dev/null || true"
  else
    # RHEL/Rocky 9+ ship python3.11 via an AppStream module (or as a package on EL10).
    runb "dnf module enable -y python3.11 2>/dev/null || true"
    runb "dnf install -y python3.11-pip 2>/dev/null || dnf install -y python3-pip 2>/dev/null || true"
  fi
  PYBIN="python3.11"
fi
[ "$DRY_RUN" = "1" ] || command -v "$PYBIN" >/dev/null 2>&1 || die "$PYBIN not found after install."
if [ "$DRY_RUN" = "0" ]; then
  echo "   using: $PYBIN -> $(${PYBIN} --version 2>&1)"
else
  echo "   using: $PYBIN (dry-run)"
fi

# ── 2. service account ──────────────────────────────────────────────────────
echo "==> [2/9] ensuring service account '${USER_NAME}'"
if id "$USER_NAME" >/dev/null 2>&1; then
  echo "   user '${USER_NAME}' already exists — keeping it."
else
  run useradd -r -m -d "/opt/${USER_NAME}" -s /usr/sbin/nologin "$USER_NAME"
fi

# ── 3. directories ──────────────────────────────────────────────────────────
echo "==> [3/9] creating data directories"
for d in "$APP" "$WORKING" "$LOGS" "$SHARED"; do
  run mkdir -p "$d"
done
runb "chown -R ${USER_NAME}:${GROUP_NAME} '${APP}' '${WORKING}' '${LOGS}' '${SHARED}'"

# ── 4. deploy source ────────────────────────────────────────────────────────
if [ -n "$SOURCE" ]; then
  echo "==> [4/9] copying local source ${SOURCE} -> ${SRC}"
  [ -d "$SOURCE" ] || die "source directory not found: $SOURCE"
  run mkdir -p "$SRC"
  run cp -a "$SOURCE/." "$SRC/"
  runb "chown -R ${USER_NAME}:${GROUP_NAME} '${SRC}'"
  echo "   copied. (update.sh can still pull from origin later if this is a git repo.)"
else
  echo "==> [4/9] cloning CORRECT (${BRANCH}) -> ${SRC}"
  if [ -d "${SRC}/.git" ]; then
    echo "   ${SRC} already cloned — updating."
    runb "runuser -u ${USER_NAME} -- git -C '${SRC}' fetch --quiet origin"
    runb "runuser -u ${USER_NAME} -- git -C '${SRC}' checkout '${BRANCH}'"
    runb "runuser -u ${USER_NAME} -- git -C '${SRC}' pull --ff-only origin '${BRANCH}'"
  else
    runb "runuser -u ${USER_NAME} -- git clone --branch '${BRANCH}' '${REPO}' '${SRC}'"
  fi
fi

# ── 5. venv + dependencies ─────────────────────────────────────────────────
echo "==> [5/9] creating virtualenv and installing dependencies"
if [ ! -x "${VENV}/bin/python" ]; then
  runb "runuser -u ${USER_NAME} -- ${PYBIN} -m venv '${VENV}'"
fi
# Ensure pip is present in the venv (some distros ship venv without pip / strip ensurepip).
if [ "$DRY_RUN" = "0" ] && ! runuser -u "${USER_NAME}" -- "${VENV}/bin/pip" --version >/dev/null 2>&1; then
  echo "   venv has no pip — bootstrapping (ensurepip, then get-pip.py fallback)."
  runuser -u "${USER_NAME}" -- "${VENV}/bin/python" -m ensurepip --upgrade 2>/dev/null || {
    GETPIP="$(mktemp /tmp/correct-getpip.XXXXXX.py)"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$GETPIP"
    runuser -u "${USER_NAME}" -- "${VENV}/bin/python" "$GETPIP"
    rm -f "$GETPIP"
  }
fi
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install --upgrade pip"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -r '${SRC}/requirements.txt'"
runb "runuser -u ${USER_NAME} -- '${VENV}/bin/pip' install -e '${SRC}'"

# ── 6. configuration ────────────────────────────────────────────────────────
echo "==> [6/9] installing config"
if [ -n "$CUSTOM_CONFIG" ]; then
  [ -f "$CUSTOM_CONFIG" ] || die "custom config not found: $CUSTOM_CONFIG"
  runb "cp '${CUSTOM_CONFIG}' '${CONFIG}'"
elif [ -f "$CONFIG" ]; then
  echo "   ${CONFIG} already exists — preserving it (edit by hand to change settings)."
else
  runb "cp '${SRC}/install/config.example.yaml' '${CONFIG}'"
  echo "   Installed template config -> ${CONFIG}"
  echo "   EDIT IT NOW: set dicom_destination, anonymization.site_code, etc."
fi
runb "chown ${USER_NAME}:${GROUP_NAME} '${CONFIG}'; chmod 640 '${CONFIG}'"

# ── 7. systemd unit ─────────────────────────────────────────────────────────
echo "==> [7/9] writing systemd unit ${UNIT}"
if [ "$DRY_RUN" = "1" ]; then
  echo "   [dry-run] would write ${UNIT}"
else
  cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=CORRECT DICOM RT Structure Set Processor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
Group=${GROUP_NAME}
WorkingDirectory=${SRC}
Environment="PYTHONUNBUFFERED=1"
ExecStart=${VENV}/bin/python ${SRC}/main.py --config ${CONFIG}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=correct
ReadWritePaths=${WORKING} ${LOGS} ${SHARED}

[Install]
WantedBy=multi-user.target
UNIT_EOF
  chmod 644 "$UNIT"
fi

# ── 8. firewall ─────────────────────────────────────────────────────────────
echo "==> [8/9] configuring firewall for port ${PORT}/tcp"
if [ "$FAMILY" = "debian" ]; then
  runb "apt-get install -y ufw 2>/dev/null || true"
  if [ "$DRY_RUN" = "0" ] && command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    runb "ufw allow ${PORT}/tcp"
  elif [ "$DRY_RUN" = "0" ] && command -v ufw >/dev/null 2>&1; then
    echo "   ufw is inactive — adding rule but NOT auto-enabling (to avoid SSH lockout)."
    runb "ufw allow ${PORT}/tcp 2>/dev/null || true"
    echo "   To enable safely: sudo ufw allow 22/tcp && sudo ufw allow ${PORT}/tcp && sudo ufw enable"
  else
    runb "ufw allow ${PORT}/tcp 2>/dev/null || true"
  fi
else
  if systemctl is-active --quiet firewalld 2>/dev/null || [ "$DRY_RUN" = "1" ]; then
    runb "firewall-cmd --permanent --add-port=${PORT}/tcp"
    runb "firewall-cmd --reload"
  else
    echo "   firewalld is not active — skipping (open port ${PORT}/tcp manually if needed)."
  fi
fi

# ── 9. enable + start ───────────────────────────────────────────────────────
echo "==> [9/9] enabling and starting 'correct' service"
run systemctl daemon-reload
run systemctl enable correct
if [ "$DRY_RUN" = "1" ]; then
  echo "   [dry-run] would start correct"
else
  systemctl restart correct 2>/dev/null || echo "   (service did not start — check: journalctl -u correct -n 30)"
fi

echo
echo "==================== INSTALL COMPLETE ===================="
echo "  Service:   systemctl status correct"
echo "  Logs:      journalctl -u correct -f"
echo "  Config:    ${CONFIG}   <-- EDIT if you used the template"
echo "  Listener:  ss -tlnp | grep ${PORT}"
echo "  Test:      echoscu -aet SENDER -aec CORRECT <this-host> ${PORT}"
echo "=========================================================="
[ "$DRY_RUN" = "1" ] && echo "(dry-run: nothing was actually changed)"