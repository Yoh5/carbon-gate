"""Le point d'entrée en ligne de commande, pour qu'un pipeline puisse l'appeler.

    python -m carbongate --help
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
