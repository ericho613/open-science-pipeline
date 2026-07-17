#!/usr/bin/env bash
set -e

GROBID_URL="${GROBID_URL:-http://grobid:8070}"

echo "Waiting for GROBID at ${GROBID_URL}/api/isalive ..."
until curl -sf "${GROBID_URL}/api/isalive" >/dev/null 2>&1; do
  echo "GROBID not ready yet - retrying in 5s"
  sleep 5
done

echo "GROBID is ready. Starting pipeline."
exec "$@"