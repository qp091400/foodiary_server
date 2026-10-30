"""Deprecated preparation-only alias. Use python3 -m scripts.prepare_recipes."""
from scripts.prepare_recipes import main


if __name__ == '__main__':
    print('Use python3 -m scripts.prepare_recipes. This command now prepares data only.')
    main()
