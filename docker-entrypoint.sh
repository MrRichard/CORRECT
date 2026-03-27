#!/bin/bash
set -e

# Fix ownership of mounted volumes so appuser can write to them.
# This is necessary because Docker creates host-side volume directories as root
# if they don't already exist before 'docker compose up'.
chown -R appuser:appgroup \
    /home/appuser/CORRECT_working \
    /home/appuser/CORRECT_logs \
    /mnt/shared \
    2>/dev/null || true

exec gosu appuser "$@"
