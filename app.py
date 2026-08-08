"""
RDTII Extraction Engine — UI entry point.

Run:
    python app.py          # opens http://localhost:7860

All UI code lives in the UI/ package (one module per screen — see UI/__init__.py
for the map). This file only starts the server.
"""

from UI import launch

if __name__ == "__main__":
    launch()
