"""Run the WP-I management browser regression on a new synthetic FastAPI database."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, choices=(100, 1000, 10000), default=1000)
    parser.add_argument('--suite', choices=('browser','workflows','zoom'), default='browser')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    ready = threading.Event()
    with (args.out / 'server.log').open('wb') as server_log:
        server = subprocess.Popen([sys.executable, str(here / 'server.py'), '--count', str(args.count), '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, 'NOTIFICATION_MOCK_MODE': 'permanent_failure' if args.suite=='retry' else 'success'})
        def drain():
            for line in iter(server.stdout.readline, b''):
                server_log.write(line)
                server_log.flush()
                if b'Uvicorn running on' in line:
                    ready.set()
        reader = threading.Thread(target=drain)
        reader.start()
        try:
            if not ready.wait(180):
                raise RuntimeError('Synthetic server did not start; inspect server.log')
            env = {**os.environ, 'I5C_PYTHON':sys.executable,'WP_H_ORIGIN': f'http://127.0.0.1:{port}', 'COUNT': str(args.count), 'OUT': str(args.out.resolve())}
            with (args.out / 'card.log').open('w') as log:
                result = subprocess.run(['node', str(here / (args.suite + '.cjs'))], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            print(f'WP-I management: count={args.count}, exit={result.returncode}', flush=True)
            return result.returncode
        finally:
            if server.poll() is None:
                server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            reader.join(timeout=10)
            server.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
