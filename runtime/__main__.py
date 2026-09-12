"""Allow ``python -m runtime <subcommand>`` without installing a script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
