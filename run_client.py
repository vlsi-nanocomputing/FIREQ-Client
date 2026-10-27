"""Entry point for the FIREQ client (same as the ``fireq-client`` command)."""

import sys

from FIREQ_CLIENT.cli import main

if __name__ == "__main__":
    sys.exit(main())
