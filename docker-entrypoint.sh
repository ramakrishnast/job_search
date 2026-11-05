#!/bin/sh
# Make the bind-mounted data dir writable for the unprivileged app user, then drop root.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR:-/data}"
  chown -R jobpilot:jobpilot "${DATA_DIR:-/data}"
  exec setpriv --reuid=jobpilot --regid=jobpilot --init-groups "$@"
fi
exec "$@"
