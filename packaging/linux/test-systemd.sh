#!/bin/sh
#
# test-systemd.sh — check the packaged unit and maintainer scripts under a real systemd.
# Run inside a privileged container booted with systemd as PID 1 (see the Installers
# workflow, job linux-systemd-test), as root:
#
#   sh packaging/linux/test-systemd.sh dist/correct-<ver>-1.x86_64.rpm
#
# Checks: enabled-but-not-started after install, C-ECHO through systemd as 'correct',
# upgrade restarts a running service, clean stop, remove stops + disables + drops unit.
set -eu
PKG="$1"; fail() { echo "FAIL: $*"; journalctl -u correct --no-pager -n 40 || true; exit 1; }
case "$PKG" in
  *.deb) cp "$PKG" /tmp/; apt-get update -qq >/dev/null; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /tmp/$(basename "$PKG") >/dev/null 2>&1 || fail install
         RM="apt-get remove -y -qq correct"; RE="apt-get install -y -qq --reinstall /tmp/$(basename "$PKG")";;
  *.rpm) dnf install -y -q "$PKG" >/dev/null || fail install; RM="dnf remove -y -q correct"; RE="dnf reinstall -y -q $PKG";;
esac
[ "$(systemctl is-enabled correct)" = enabled ] || fail "not enabled after install"
! systemctl is-active -q correct || fail "started before configuration"
echo "   enabled, not started: ok"
systemctl start correct
echo_ok() { /opt/correct/runtime/bin/python3 -I -c "
import sys
from pynetdicom import AE
from pynetdicom.sop_class import Verification
ae = AE(); ae.add_requested_context(Verification)
a = ae.associate('127.0.0.1', 11112, ae_title='CORRECT')
ok = a.is_established and a.send_c_echo().Status == 0
a.is_established and a.release(); sys.exit(0 if ok else 1)" >/dev/null 2>&1; }
n=0; until echo_ok; do n=$((n+1)); [ $n -lt 60 ] || fail "no C-ECHO via systemd"; sleep 1; done
echo "   C-ECHO via systemd after ${n}s"
[ "$(ps -o user= -p "$(systemctl show -p MainPID --value correct)")" = correct ] || fail "not running as correct"
echo "   runs as: correct"
$RE >/dev/null 2>&1 || fail reinstall
sleep 2; systemctl is-active -q correct || fail "not running after upgrade (try-restart)"
n=0; until echo_ok; do n=$((n+1)); [ $n -lt 60 ] || fail "no C-ECHO after upgrade"; sleep 1; done
echo "   upgrade restarted service: ok"
systemctl stop correct; r=$(systemctl show -p Result --value correct); [ "$r" = success ] || fail "stop result $r"
echo "   clean stop: ok"
systemctl start correct; sleep 1
$RM >/dev/null 2>&1 || fail remove
! systemctl is-active -q correct || fail "still running after remove"
[ ! -e /usr/lib/systemd/system/correct.service ] || fail "unit left"
echo "   remove stopped + disabled: ok"
echo "SYSTEMD PASS: $(basename "$PKG")"
