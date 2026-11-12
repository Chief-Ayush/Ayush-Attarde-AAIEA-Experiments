"""CampusHire - a rule-based campus-hiring workflow (Experiment 7) that runs every step through MCP tools."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:          # the manual's modules (recruitment_workflow.py, notifications.py) live in the project root
    sys.path.insert(0, str(ROOT))
__version__ = "1.0.0"
