#!/bin/sh
# CORRECT package post-remove.
#   deb: $1 = remove | purge | upgrade | ...        rpm: $1 = 0 (erase) | >=1 (upgrade)
set -e
case "${1:-}" in
  remove|purge|0)
    if [ -d /run/systemd/system ]; then
      systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    # Anything Python left behind (e.g. __pycache__ from a manual run as root).
    rm -rf /opt/correct/runtime /opt/correct/app
    rmdir /opt/correct 2>/dev/null || true
    if [ "$1" != purge ]; then
      cat <<'MSG'
CORRECT removed. Data was kept (it may contain patient data and the PID mapping):
  /var/lib/correct  /var/log/correct  and the config in /etc/correct (if changed).
Delete them by hand when no longer needed. The 'correct' user was also kept.
MSG
    fi
    ;;
esac
exit 0
