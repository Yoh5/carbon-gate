"""Le point d'entrée en ligne de commande, pour qu'un pipeline puisse l'appeler.

    python -m carbongate --help
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print("Pour l'instant, la démonstration complète :")
        print("    python exemple.py")
        return 0
    from exemple import main as demonstration
    return demonstration()


if __name__ == "__main__":
    raise SystemExit(main())
