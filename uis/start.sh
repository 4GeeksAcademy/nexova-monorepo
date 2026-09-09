#!/bin/sh
set -e

trap 'kill -TERM $website_pid $backoffice_pid 2>/dev/null' TERM INT

(cd /app/uis/website && exec ./node_modules/.bin/next dev --webpack -p 3000 -H 0.0.0.0) &
website_pid=$!

(cd /app/uis/backoffice && exec ./node_modules/.bin/next dev --webpack -p 3001 -H 0.0.0.0) &
backoffice_pid=$!

wait $website_pid $backoffice_pid