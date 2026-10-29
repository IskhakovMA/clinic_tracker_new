"""WP-D: локальный браузерный стенд, всегда с новой синтетической базой.

Из корня worktree: python tools/tests/wp_d_synthetic_server.py
Никогда не использует переданный извне CLINIC_DATA_DIR.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18427)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    data = Path(tempfile.mkdtemp(prefix="clinic-wpd-synthetic-"))
    os.environ["CLINIC_DATA_DIR"] = str(data)
    os.environ["CLINIC_DEBUG"] = "false"
    os.environ["NOTIFICATION_PROVIDER"] = "mock"
    os.environ["CLINIC_AI_MODE"] = "disabled"
    from backend.app import db, users
    import uvicorn

    conn = db.get_connection()
    users.create_user(conn, username="synthetic.browser", full_name="Синтетический Сотрудник",
                      role_id="developer", password="synthetic-browser-password", must_change_password=False)
    db.reset_singleton_for_tests()
    print(f"Только синтетические данные: {data}", flush=True)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
