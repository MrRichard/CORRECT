#!/bin/sh
# CORRECT package pre-install. Runs before files are unpacked.
#   deb: $1 = install | upgrade        rpm: $1 = 1 (install) | >=2 (upgrade)
set -e

# Refuse to install over an install.sh / OVA deployment (venv + git checkout in
# /opt/correct/app, unit in /etc/systemd/system). Mixing the two would leave the old
# unit overriding the packaged one and two copies of the code under /opt/correct/app.
if [ -d /opt/correct/app/venv ] || [ -d /opt/correct/app/src ]; then
  cat >&2 <<'MSG'

ERROR: CORRECT is already installed here by install/install.sh (or the OVA image):
       /opt/correct/app/venv or /opt/correct/app/src exists.

Remove that installation's service first, keeping its data, then move it aside:

    sudo /opt/correct/app/src/install/uninstall.sh      # removes the service only
    sudo mv /opt/correct/app /opt/correct/app.legacy

Then install this package again. Afterwards, copy your settings from
/opt/correct/app.legacy/config.yaml into /etc/correct/config.yaml, and copy
pid_mapping.json from the old reports folder (default /mnt/shared) to keep the same
anonymized patient IDs. See docs/installation-linux-packages.md ("Migrating").

MSG
  exit 1
fi

if ! getent group correct >/dev/null 2>&1; then
  groupadd --system correct
fi
if ! getent passwd correct >/dev/null 2>&1; then
  nologin=/usr/sbin/nologin
  [ -x "$nologin" ] || nologin=/sbin/nologin
  useradd --system --gid correct --home-dir /var/lib/correct --no-create-home \
          --shell "$nologin" --comment "CORRECT DICOM processor" correct
fi
exit 0
