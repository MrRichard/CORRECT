#!/bin/sh
#
# test-package.sh — install a CORRECT .deb/.rpm in a clean distro and exercise it.
# Meant for throwaway containers (CI and local), run as root:
#
#   docker run --rm -v "$PWD:/src:ro" ubuntu:24.04   sh /src/packaging/linux/test-package.sh /src/dist/correct_0.3.0-1_amd64.deb
#   docker run --rm -v "$PWD:/src:ro" rockylinux:9   sh /src/packaging/linux/test-package.sh /src/dist/correct-0.3.0-1.x86_64.rpm
#
# Checks: install (incl. scriptlets), layout + ownership, bundled imports (no system libs
# such as libGL), the app answering C-ECHO as the 'correct' user, clean SIGTERM shutdown,
# reinstall (upgrade path) keeping a modified config, and removal keeping data.
#
set -eu
PKG="$1"
PORT=11112
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo "==> $*"; }

[ -f "$PKG" ] || fail "package not found: $PKG"
. /etc/os-release
echo "Testing $(basename "$PKG") on ${PRETTY_NAME:-$ID}"

case "$PKG" in
  *.deb)
    export DEBIAN_FRONTEND=noninteractive
    install_pkg() { apt-get install -y -qq "$1" >/tmp/pm.log 2>&1 || { cat /tmp/pm.log; return 1; }; }
    reinstall_pkg() { apt-get install -y -qq --reinstall "$1" >/tmp/pm.log 2>&1 || { cat /tmp/pm.log; return 1; }; }
    remove_pkg() { apt-get remove -y -qq correct; }
    apt-get update -qq >/dev/null
    cp "$PKG" /tmp/ && PKG="/tmp/$(basename "$PKG")"   # apt wants a readable local path
    ;;
  *.rpm)
    PM=dnf; command -v dnf >/dev/null 2>&1 || PM=yum
    install_pkg() { $PM install -y -q "$1" || return 1; }
    reinstall_pkg() { $PM reinstall -y -q "$1"; }
    remove_pkg() { $PM remove -y -q correct; }
    ;;
  *) fail "unknown package type: $PKG";;
esac

step "install"
install_pkg "$PKG" || fail "package install failed"

step "layout"
getent passwd correct >/dev/null || fail "user 'correct' missing"
for d in /var/lib/correct/working /var/lib/correct/shared /var/log/correct; do
  [ "$(stat -c %U:%G "$d")" = "correct:correct" ] || fail "$d not owned by correct"
done
[ "$(stat -c %a:%G /etc/correct/config.yaml)" = "640:correct" ] || fail "config perms: $(stat -c %a:%U:%G /etc/correct/config.yaml)"
[ -f /usr/lib/systemd/system/correct.service ] || fail "systemd unit missing"
grep -q '/var/lib/correct/shared/pid_mapping.json' /etc/correct/config.yaml || fail "config paths not rewritten"
! grep -qE '/opt/correct/(working|logs)|/mnt/shared' /etc/correct/config.yaml || fail "config has install.sh paths"

step "bundled runtime (isolated from the system)"
/opt/correct/runtime/bin/python3 -I -c "
import cv2, numpy, scipy, skimage, matplotlib, pydicom, pynetdicom, highdicom, rt_utils, ruamel.yaml, watchdog, _tkinter
import netrt_core.app, correct_admin.config_model
from netrt_core.platform_paths import default_config_path
assert default_config_path() == '/etc/correct/config.yaml', default_config_path()
print('   python', __import__('sys').version.split()[0], '| cv2', cv2.__version__)
" || fail "bundled imports"

step "run as 'correct', C-ECHO, SIGTERM"
cd /var/lib/correct
# Drop to 'correct' and exec the wrapper (minimal images have no runuser/su), so $PID is
# the app itself and receives SIGTERM directly — as it does from systemd.
PYTHONDONTWRITEBYTECODE=1 /opt/correct/runtime/bin/python3 -I -c "
import os, pwd, sys
p = pwd.getpwnam('correct')
os.setgroups([]); os.setgid(p.pw_gid); os.setuid(p.pw_uid)
os.environ['HOME'] = p.pw_dir
os.execv(sys.argv[1], sys.argv[1:])" /usr/bin/correct >/tmp/correct.out 2>&1 &
PID=$!
ok=0
for i in $(seq 1 60); do
  if /opt/correct/runtime/bin/python3 -I -c "
import sys
from pynetdicom import AE
from pynetdicom.sop_class import Verification
ae = AE(ae_title='TESTSCU'); ae.add_requested_context(Verification)
a = ae.associate('127.0.0.1', $PORT, ae_title='CORRECT')
ok = a.is_established and a.send_c_echo().Status == 0
if a.is_established: a.release()
sys.exit(0 if ok else 1)" >/dev/null 2>&1; then ok=1; echo "   C-ECHO OK after ${i}s"; break; fi
  kill -0 "$PID" 2>/dev/null || break
  sleep 1
done
[ "$ok" = 1 ] || { cat /tmp/correct.out; fail "no C-ECHO response"; }
# Wait for a clean exit (exit code 0, shutdown logged).
kill -TERM "$PID"; rc=0; wait "$PID" || rc=$?
[ "$rc" = 0 ] || { cat /tmp/correct.out; fail "exit code $rc after SIGTERM"; }
grep -q "NETRT Application shut down" /var/log/correct/application.log || fail "no clean-shutdown log line"
[ "$(stat -c %U /var/log/correct/application.log)" = correct ] || fail "log not written by 'correct'"

step "reinstall keeps a modified config"
sed -i 's/site_code: .*/site_code: TESTSITE/' /etc/correct/config.yaml
reinstall_pkg "$PKG" || fail "reinstall failed"
grep -q 'site_code: TESTSITE' /etc/correct/config.yaml || fail "config overwritten on reinstall"

step "remove keeps data"
remove_pkg >/tmp/remove.log 2>&1 || { cat /tmp/remove.log; fail "remove failed"; }
[ ! -e /opt/correct/runtime ] || fail "/opt/correct/runtime left behind"
[ ! -e /usr/bin/correct ] || fail "/usr/bin/correct left behind"
[ -f /var/log/correct/application.log ] || fail "logs were deleted on remove"

echo "PASS: $(basename "$PKG") on ${PRETTY_NAME:-$ID}"
