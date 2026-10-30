"""Изолированный FastAPI-стенд WP-E: новая база, mock, фиксированные часы учреждения."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18435)
    parser.add_argument('--now', default='2026-10-01T11:00:00+03:00')
    args = parser.parse_args()
    moment = datetime.fromisoformat(args.now)
    if moment.tzinfo is None:
        parser.error('--now requires a UTC offset')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    data = Path(tempfile.mkdtemp(prefix='clinic-wpe-synthetic-'))
    os.environ['CLINIC_DATA_DIR'] = str(data)
    os.environ['CLINIC_DEBUG'] = 'false'
    os.environ['NOTIFICATION_PROVIDER'] = 'mock'
    os.environ['CLINIC_AI_MODE'] = 'disabled'
    from backend.app import clock, db, users
    import uvicorn
    clock.now_moscow = lambda: moment.astimezone(clock.institution_tz())
    from backend.app import operations_state
    operations_state.bootstrap_database(confirm_new=True)
    operations_state.verify_database(confirm_offline=True)
    conn = db.get_connection()
    users.create_user(conn, username='synthetic.browser', full_name='Синтетический Сотрудник',
                      role_id='developer', password='synthetic-browser-password', must_change_password=False)
    db.reset_singleton_for_tests()
    print(f'Только синтетические данные: {data}; время учреждения: {clock.now_moscow().isoformat()}', flush=True)
    uvicorn.run('backend.app.main:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
