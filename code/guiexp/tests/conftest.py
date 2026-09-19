import sys
from pathlib import Path

# Make the guiexp package (in computer-use/computer-use/) importable when
# pytest is invoked from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
