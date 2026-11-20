#!/bin/sh
# CORRECT package pre-remove.
#   deb: $1 = remove | upgrade | deconfigure        rpm: $1 = 0 (erase) | >=1 (upgrade)
set -e
case "${1:-}" in
  remove|0)
    if [ -d /run/systemd/system ]; then
      systemctl disable --now correct.service >/dev/null 2>&1 || true
    fi
    ;;
esac
exit 0
