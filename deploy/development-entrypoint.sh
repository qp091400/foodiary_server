#!/bin/sh
set -eu
if [ ! -f /opt/recipe-seed/manifest.json ]; then
    echo 'Recipe seed missing. Start with: python3 -m scripts.start_with_recipes' >&2
    exit 1
fi
python -m scripts.initialize_recipe_data
exec "$@"
