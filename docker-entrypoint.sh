#!/bin/sh
set -e

# data/ er normalt et montert volum. Ved bind-mount til en tom katalog
# forsvinner product_types.json som er bakt inn i imaget, så vi sår den inn
# på nytt hvis den mangler. Alt annet i data/ (CSV, session.json, ordre-JSON)
# bygges opp av appen selv.
mkdir -p /app/data
if [ ! -f /app/data/product_types.json ] && [ -f /app/seed/product_types.json ]; then
  cp /app/seed/product_types.json /app/data/product_types.json
fi

exec uv run min-oda --host 0.0.0.0 --port 8000 --no-reload "$@"
