"""Run each requested synthetic browser experiment once, with an ephemeral DB."""
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
    parser.add_argument('experiment', choices=('browser', 'drafts', 'zoom', 'benchmark'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    args.out.mkdir(parents=True, exist_ok=True)
    for count in ((100, 1000, 10000) if args.experiment == 'benchmark' else (100,)):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        ready = threading.Event()
        with (args.out / f'{args.experiment}-{count}-server.log').open('wb') as server_log:
            server = subprocess.Popen([sys.executable, str(here / 'server.py'), '--count', str(count), '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
                    raise RuntimeError('Synthetic server did not become ready; inspect its log')
                env = {**os.environ, 'WP_H_ORIGIN': f'http://127.0.0.1:{port}', 'COUNT': str(count), 'OUT': str(args.out.resolve()), 'WP_H_EVIDENCE_OUT': str(args.out.resolve())}
                with (args.out / f'{args.experiment}-{count}.log').open('w') as log:
                    try:
                        result = subprocess.run(
                            ['node', str(here / (args.experiment + '.cjs'))],
                            env=env, stdout=log, stderr=subprocess.STDOUT,
                            timeout=150 if args.experiment == 'browser' else None,
                        )
                    except subprocess.TimeoutExpired:
                        log.write('WP-H browser runner exceeded 150 seconds before completing.\n')
                        print('browser: host deadline exceeded; inspect browser log', file=sys.stderr)
                        return 124
                print(f'{args.experiment}: count={count}, exit={result.returncode}', flush=True)
                if result.returncode:
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
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
