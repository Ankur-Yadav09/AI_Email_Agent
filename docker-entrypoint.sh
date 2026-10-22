#!/bin/sh
# Start as root only long enough to make the writable folders belong to the app user, then drop privileges.
# Needed because volumes created by an older (root) container, and bind mounts such as ./logs, keep their old owner.
for dir in "${DATA_DIR:-/data}" /app/logs /app/data; do
    mkdir -p "$dir" 2>/dev/null
    chown -R app:app "$dir" 2>/dev/null || true
done
exec gosu app "$@"
