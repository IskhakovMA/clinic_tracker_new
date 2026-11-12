"""Reuse the accepted temporary synthetic FastAPI fixture."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).resolve().parents[3]/'tools/tests/wp_i_ai/server.py'),run_name='__main__')
