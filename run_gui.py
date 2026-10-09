"""Entry point for the FIREQ graphical experiment designer.

Usage:
    python run_gui.py [experiment.yaml]
"""

import sys

from FIREQ_GUI.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
