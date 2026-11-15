#!/usr/bin/env bash
# Run the SQL policy tests of supabase/tests against a scratch Postgres 17 database: the Supabase stub, every migration
# in file-name order, then every test file. Stops at the first error (a failed assert or any other statement).
#
#   docker run -d --rm --name misra-sql -e POSTGRES_HOST_AUTH_METHOD=trust -p 54330:5432 postgres:17-alpine
#   scripts/run-sql-tests.sh postgresql://postgres@localhost:54330/postgres
#
# The database must be empty: the stub creates roles and an `auth` schema. Never point this at a Supabase project.
set -euo pipefail

url="${1:?usage: scripts/run-sql-tests.sh <database url>}"
cd "$(dirname "$0")/.."

run() {
  echo "==> $1"
  psql "$url" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file "$1"
}

run supabase/ci/supabase_stub.sql
migrations=(supabase/migrations/*.sql)
tests=(supabase/tests/*.sql)
[ -e "${migrations[0]}" ] && [ -e "${tests[0]}" ] || { echo "no migrations or no tests found" >&2; exit 1; }
for file in "${migrations[@]}"; do run "$file"; done
for file in "${tests[@]}"; do run "$file"; done
echo "all ${#tests[@]} SQL test files passed"
