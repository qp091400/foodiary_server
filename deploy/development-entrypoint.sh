#!/bin/sh
set -eu
if [ ! -f /opt/recipe-seed/manifest.json ]; then
    echo 'Recipe seed missing. Prepare with: python3 -m scripts.prepare_recipes; then docker compose up --build -d' >&2
    exit 1
fi
python -m scripts.initialize_recipe_data
exec "$@"
