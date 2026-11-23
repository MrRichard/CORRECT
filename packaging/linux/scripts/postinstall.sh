#!/bin/sh
# CORRECT package post-install.
#   deb: $1 = configure, $2 = previously configured version ("" on first install)
#   rpm: $1 = 1 (install) | >=2 (upgrade)
set -e

first_install=0
case "${1:-}" in
  configure) [ -z "${2:-}" ] && first_install=1 ;;
  1)         first_install=1 ;;
esac

# Data folders (may hold PHI): service account only.
for d in /var/lib/correct /var/lib/correct/working /var/lib/correct/shared /var/log/correct; do
  install -d -m 0750 -o correct -g correct "$d"
done
# Config is root-owned and readable by the service group.
if [ -f /etc/correct/config.yaml ]; then
  chgrp correct /etc/correct/config.yaml
  chmod 0640 /etc/correct/config.yaml
fi

if [ -d /run/systemd/system ]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [ "$first_install" = 1 ]; then
    # Enabled for boot, but not started until the destination/site code are configured.
    systemctl enable correct.service >/dev/null 2>&1 || true
  else
    systemctl try-restart correct.service >/dev/null 2>&1 || true
  fi
fi

if [ "$first_install" = 1 ]; then
  cat <<'MSG'

CORRECT is installed. Next steps:
  1. Edit /etc/correct/config.yaml: dicom_destination (ip, port, ae_title) and
     anonymization.site_code.   (Graphical editor: sudo correct-config)
  2. sudo systemctl start correct          (already enabled at boot)
  3. Open the DICOM port if a firewall is active, e.g.
       sudo firewall-cmd --permanent --add-port=11112/tcp && sudo firewall-cmd --reload
       sudo ufw allow 11112/tcp
  Logs: journalctl -u correct -f   and   /var/log/correct/

MSG
fi
exit 0
