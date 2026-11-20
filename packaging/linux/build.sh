#!/usr/bin/env bash
#
# build.sh — build the CORRECT .deb and .rpm (self-contained: bundled Python + dependencies).
#
#   1. Downloads the pinned python-build-standalone CPython (runtime.lock), verifies SHA256.
#   2. Installs requirements.txt into it, resolving wheels for the MANYLINUX baseline so the
#      result runs on older distros than the build host.
#   3. Stages the CORRECT source under /opt/correct/app, linked in with a relative .pth file.
#   4. Precompiles everything (hash-based .pyc, so nothing is written under /opt at runtime).
#   5. Packages the staged tree with nFPM into dist/correct_<ver>_amd64.deb and
#      dist/correct-<ver>-1.x86_64.rpm.
#
# Runs on any x86_64 Linux with bash, curl, tar and sha256sum (no root, no system Python):
#     packaging/linux/build.sh                # build both packages
#     packaging/linux/build.sh --stage-only   # stage only (build/linux/stage)
#     packaging/linux/build.sh --format deb   # just one of deb | rpm
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BUILD="$ROOT/build/linux"
CACHE="$BUILD/cache"
STAGE="$BUILD/stage"
OUT_DIR="${OUT_DIR:-$ROOT/dist}"
PREFIX="/opt/correct"
FORMATS="deb rpm"
STAGE_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --stage-only) STAGE_ONLY=1; shift;;
    --format) FORMATS="$2"; shift 2;;
    -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \?//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

[ "$(uname -m)" = "x86_64" ] || die "build on x86_64 (the packages bundle an x86_64 Python)"

# ── version + pins ──────────────────────────────────────────────────────────
VERSION="$(sed -n 's/^__version__[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/netrt_core/__init__.py")"
[ -n "$VERSION" ] || die "cannot read netrt_core.__version__"
lock() {
  local v
  v="$(sed -n "s/^[[:space:]]*$1=\(.*\)$/\1/p" "$HERE/runtime.lock" | tr -d '[:space:]')"
  [ -n "$v" ] || die "runtime.lock is missing $1"
  printf '%s' "$v"
}
PBS_RELEASE="$(lock PBS_RELEASE)"; PYTHON_VERSION="$(lock PYTHON_VERSION)"
ASSET="$(lock ASSET)"; SHA256="$(lock SHA256)"; MANYLINUX="$(lock MANYLINUX)"
NFPM_VERSION="$(lock NFPM_VERSION)"; NFPM_SHA256="$(lock NFPM_SHA256)"
PY_MM="${PYTHON_VERSION%.*}"          # 3.11
echo "CORRECT $VERSION  |  Python $PYTHON_VERSION (python-build-standalone $PBS_RELEASE)  |  $MANYLINUX"

fetch() {  # fetch URL FILE SHA256
  local url="$1" file="$2" want="$3" got
  if [ ! -f "$file" ]; then
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  got="$(sha256sum "$file" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    rm -f "$file"
    die "SHA256 mismatch for $(basename "$file"): got $got, expected $want"
  fi
}

# ── 1. runtime ──────────────────────────────────────────────────────────────
step "Fetching Python runtime"
mkdir -p "$CACHE"
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${ASSET//+/%2B}" \
      "$CACHE/$ASSET" "$SHA256"

rm -rf "$STAGE"
RUNTIME="$STAGE$PREFIX/runtime"
APP="$STAGE$PREFIX/app"
mkdir -p "$RUNTIME" "$APP"
tar -xzf "$CACHE/$ASSET" -C "$RUNTIME" --strip-components=1   # archive top dir is python/
PY="$RUNTIME/bin/python3"
SP="$RUNTIME/lib/python$PY_MM/site-packages"

# ── 2. dependencies ─────────────────────────────────────────────────────────
step "Installing Python dependencies into the runtime ($MANYLINUX wheels)"
# pip does not expand --platform to older compatible tags, so list every manylinux tag up
# to the baseline (manylinux_2_17 == manylinux2014, _2_12 == 2010, _2_5 == manylinux1).
GLIBC_MAX="$(printf '%s' "$MANYLINUX" | sed -n 's/^manylinux_2_\([0-9]*\)_x86_64$/\1/p')"
[ -n "$GLIBC_MAX" ] || die "MANYLINUX must look like manylinux_2_NN_x86_64"
PLATFORMS=()
for ((m = GLIBC_MAX; m >= 17; m--)); do PLATFORMS+=(--platform "manylinux_2_${m}_x86_64"); done
PLATFORMS+=(--platform manylinux2014_x86_64 --platform manylinux2010_x86_64 --platform manylinux1_x86_64)
# --platform requires --target; installing straight into site-packages keeps the layout
# identical to a normal install. --only-binary: never compile against the build host.
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location --no-compile \
  --only-binary=:all: "${PLATFORMS[@]}" --implementation cp \
  --python-version "$PY_MM" --abi "cp${PY_MM/./}" \
  --target "$SP" --upgrade -r "$ROOT/requirements.txt"

# rt-utils hard-depends on opencv-python, whose cv2 links libGL/X11 (missing on headless
# servers: "ImportError: libGL.so.1"). Swap in opencv-python-headless at the same pinned
# version; CORRECT never uses OpenCV's GUI functions.
OPENCV_VERSION="$(sed -n 's/^opencv-python==\([^[:space:];]*\).*/\1/p' "$ROOT/requirements.txt")"
[ -n "$OPENCV_VERSION" ] || die "requirements.txt must pin opencv-python==<version>"
"$PY" -m pip uninstall --disable-pip-version-check -y -q opencv-python
rm -rf "$SP/cv2"
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location --no-compile --no-deps \
  --only-binary=:all: "${PLATFORMS[@]}" --implementation cp \
  --python-version "$PY_MM" --abi "cp${PY_MM/./}" \
  --target "$SP" "opencv-python-headless==$OPENCV_VERSION"

# ── 3. application source ───────────────────────────────────────────────────
step "Staging CORRECT application files"
cp -a "$ROOT/netrt_core" "$ROOT/correct_admin" "$APP/"
cp -a "$ROOT/main.py" "$ROOT/DicomAnonymizer.py" "$APP/"
# Relative .pth: resolved against site-packages -> /opt/correct/app.
printf '../../../../app\n' > "$SP/correct.pth"

# Packaged config: install/config.example.yaml with the package's paths.
mkdir -p "$STAGE/etc/correct"
sed -e 's#^\# Copied to /opt/correct/app/config.yaml by install.sh on first install.#\# Installed as /etc/correct/config.yaml by the correct .deb/.rpm (kept on upgrade).#' \
    -e 's#/opt/correct/working#/var/lib/correct/working#' \
    -e 's#/opt/correct/logs#/var/log/correct#' \
    -e 's#/mnt/shared#/var/lib/correct/shared#g' \
    "$ROOT/install/config.example.yaml" > "$STAGE/etc/correct/config.yaml"
if grep -qE '/opt/correct|/mnt/shared' "$STAGE/etc/correct/config.yaml"; then
  die "packaged config still references install.sh paths — update the sed rules in build.sh"
fi

mkdir -p "$STAGE/usr/share/doc/correct"
cp "$ROOT/docs/LICENSE" "$ROOT/docs/DISCLAIMER.md" "$ROOT/README.md" "$STAGE/usr/share/doc/correct/"

# ── 4. slim down + precompile ───────────────────────────────────────────────
step "Removing files not needed at runtime"
rm -rf "$RUNTIME/lib/python$PY_MM/test" \
       "$RUNTIME/lib/python$PY_MM/idlelib/idle_test" \
       "$RUNTIME/lib/python$PY_MM/tkinter/test" \
       "$RUNTIME/share/man"
# Console scripts carry absolute shebangs into the build tree; keep only the interpreters.
find "$RUNTIME/bin" -mindepth 1 ! -name 'python*' -delete
rm -f "$RUNTIME/bin/"python*-config
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

step "Precompiling bytecode"
# unchecked-hash: valid regardless of file mtimes; -s/-p record the installed path.
"$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash -s "$STAGE" -p / \
  "$RUNTIME/lib/python$PY_MM" "$APP" >/dev/null

# ── smoke check ─────────────────────────────────────────────────────────────
step "Smoke-testing the staged runtime"
( cd "$STAGE" && env -i PATH=/usr/bin:/bin "$PY" -I -c "
import sys, cv2, numpy, scipy, skimage, matplotlib, pydicom, pynetdicom, highdicom, rt_utils, ruamel.yaml, yaml, watchdog
import netrt_core.app, correct_admin.config_model, correct_admin.service_control
assert '/stage$PREFIX/app/' in netrt_core.app.__file__, netrt_core.app.__file__
print('runtime OK:', sys.version.split()[0], '| app from', netrt_core.app.__file__)
" ) || die "staged runtime smoke test failed"
# -I (isolated) ignores PYTHONPATH/user site, so imports above came from the bundle only.

du -sh "$STAGE$PREFIX" | sed 's/^/   staged size: /'
if [ "$STAGE_ONLY" = 1 ]; then echo "Staged at $STAGE (packages skipped)"; exit 0; fi

# ── 5. packages ─────────────────────────────────────────────────────────────
step "Fetching nFPM $NFPM_VERSION"
NFPM_TGZ="$CACHE/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz"
fetch "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz" \
      "$NFPM_TGZ" "$NFPM_SHA256"
NFPM="$CACHE/nfpm-$NFPM_VERSION"
if [ ! -x "$NFPM" ]; then
  tar -xzf "$NFPM_TGZ" -C "$CACHE" nfpm && mv "$CACHE/nfpm" "$NFPM"
fi

mkdir -p "$OUT_DIR"
# nFPM does not expand variables in contents/scripts paths, so render the config first.
NFPM_CFG="$BUILD/nfpm.yaml"
sed -e "s#\${CORRECT_VERSION}#$VERSION#g" -e "s#\${CORRECT_STAGE}#$STAGE#g" -e "s#\${CORRECT_PKG}#$HERE#g" \
    "$HERE/nfpm.yaml" > "$NFPM_CFG"
! grep -q '\${' "$NFPM_CFG" || die "unexpanded variable left in $NFPM_CFG"
for fmt in $FORMATS; do
  step "Packaging $fmt"
  "$NFPM" package --config "$NFPM_CFG" --packager "$fmt" --target "$OUT_DIR/"
done
ls -lh "$OUT_DIR"/correct[_-]"$VERSION"* 2>/dev/null
