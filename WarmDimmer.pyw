"""Warm Dimmer launcher (no console window). Double-click to start."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warmdimmer.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
