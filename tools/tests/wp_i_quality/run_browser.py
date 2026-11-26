"""Run the browser suite against the localhost-only synthetic I-8A3 FastAPI lab."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from urllib.request import urlopen


def loopback_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def command_output(*command, cwd=None):
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def command_version(*command, cwd=None):
    result = command_output(*command, cwd=cwd)
    match = re.search(rb"(?<![0-9.])[0-9]+(?:\.[0-9]+){1,3}\b", (result or b"")[:1024])
    return match[0].decode() if match else None


def failure_kind(phase, error):
    """Return a bounded diagnostic code, never exception text or local paths."""
    if isinstance(error, subprocess.TimeoutExpired):
        return 'subprocess_timeout'
    if phase == 'server_start':
        return 'server_start_failed'
    if phase == 'server_ready':
        return 'server_not_ready'
    if phase == 'healthcheck':
        return 'healthcheck_failed'
    if phase == 'temp_setup':
        return 'temporary_directory_failed'
    if phase == 'browser_test':
        return 'browser_process_failed'
    return 'runner_failed'


def browser_receipt(suite, output, process, browser_tmp, fixture_tmp, env, here,
                    failure=None, failed_phase=None):
    # Only allowlisted, non-identifying diagnostic codes and path lengths leave the runner.
    # Raw errors, browser command lines and HTTP/server logs remain in private local files.
    log_file = output / 'browser.local.log'
    try:
        log = log_file.read_bytes()
    except OSError:
        log = b''
    launch_error = re.search(rb'browserType\.launch(?:PersistentContext)?:', log)
    profile = re.search(rb'--user-data-dir=([^\r\n]+?)(?= --|\r?\n|$)', log)
    patterns = (
        ('socket_path_too_long', rb'socket.{0,45}(?:path.{0,20}too long|file name too long)'),
        ('singleton_socket', rb'ProcessSingleton|SingletonSocket'),
        ('missing_browser_binary', rb'executable (?:doesn.t exist|does not exist|not found)|ENOENT'),
        ('missing_runtime_dependency', rb'error while loading shared libraries|host system is missing dependencies'),
        ('browser_sandbox_initialization', rb'no usable sandbox|failed to move to new namespace'),
        ('browser_closed', rb'target page, context or browser has been closed|browser has disconnected'),
    )
    code = process.returncode if process is not None else 1
    if failure is not None:
        # An exception after an apparently successful browser run still fails the runner.
        code = code or 1
        phase = failed_phase if failed_phase in {
            'temp_setup', 'server_start', 'server_ready', 'healthcheck', 'browser_test',
        } else 'runner_error'
        error_type = 'RunnerException'
        reason = failure_kind(phase, failure)
    elif code:
        phase = 'browser_launch' if launch_error else 'browser_test' if process is not None else 'runner_error'
        error_type = 'PlaywrightLaunchError' if launch_error else 'BrowserSuiteError' if process is not None else 'RunnerException'
        reason = ('unclassified_launch_failure' if launch_error else 'suite_failed')
        if launch_error:
            reason = next((label for label, pattern in patterns if re.search(pattern, log, re.I)), reason)
    else:
        phase, error_type, reason = 'complete', None, None

    module = env.get('PLAYWRIGHT_MODULE', 'playwright')
    executable = env.get('WP_H_FULL_CHROMIUM') if suite == 'browser_zoom' else env.get('BROWSER_EXECUTABLE')
    if not executable:
        discovery = command_output('node', '-p', 'require(process.argv[1]).chromium.executablePath()', module, cwd=here)
        if discovery:
            executable = os.fsdecode(discovery.rstrip())
    candidate = env.get('GITHUB_SHA')
    if not candidate:
        raw = command_output('git', '-C', str(here), 'rev-parse', 'HEAD')
        candidate = os.fsdecode(raw).strip() if raw else ''
    result = {
        'suite': suite,
        'candidateSha': candidate if re.fullmatch(r'[0-9a-f]{40}', candidate) else None,
        'syntheticOnly': True,
        'phase': phase,
        'exitCode': code,
        'errorType': error_type,
        'reason': reason,
        'platform': sys.platform,
        'architecture': platform.machine(),
        'nodeVersion': command_version('node', '--version'),
        'playwrightVersion': command_version(
            'node', '-p', "require(process.argv[1] + '/package.json').version", module, cwd=here),
        'chromiumVersion': command_version(executable, '--version') if executable else None,
        'browserBinaryExists': Path(executable).is_file() if executable else None,
        'tmpdirBytes': len(os.fsencode(browser_tmp)) if browser_tmp is not None else None,
        'profilePathBytes': len(profile[1]) if profile else None,
        'browserTmpRemoved': browser_tmp is not None and not browser_tmp.exists(),
        'fixtureTmpRemoved': fixture_tmp is not None and not fixture_tmp.exists(),
    }
    (output / 'browser-execution.json').write_text(json.dumps(result, indent=2) + '\n')
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--suite', choices=('browser', 'browser_zoom', 'browser_perf'), default='browser')
    parser.add_argument('--count', default=240, type=int)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    scratch = browser_tmp = process = None
    env = dict(os.environ)
    failure = None
    phase = 'temp_setup'
    try:
        with ExitStack() as temporary:
            scratch = Path(temporary.enter_context(tempfile.TemporaryDirectory(prefix='private-fixture-', dir=output)))
            browser_tmp = scratch
            if sys.platform == 'linux':
                # Chromium ProcessSingleton requires a short Linux Unix-domain socket path.
                browser_tmp = Path(temporary.enter_context(tempfile.TemporaryDirectory(prefix='wq.', dir='/tmp')))
            fixture = scratch / 'fixture.local.json'
            ready = threading.Event()
            port = loopback_port()
            with (output / 'server.local.log').open('wb') as server_log:
                phase = 'server_start'
                server = subprocess.Popen(
                    [sys.executable, str(here / 'lab.py'), '--port', str(port),
                     '--fixture-file', str(fixture), '--scratch-dir', str(scratch),
                     '--count', str(args.count)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={
                        **os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                    },
                )

                def drain():
                    for line in iter(server.stdout.readline, b''):
                        server_log.write(line)
                        server_log.flush()
                        if b'Uvicorn running on' in line:
                            ready.set()

                reader = None
                try:
                    reader = threading.Thread(target=drain)
                    reader.start()
                    phase = 'server_ready'
                    if not ready.wait(180) or not fixture.is_file():
                        raise RuntimeError('The synthetic server did not start.')
                    phase = 'healthcheck'
                    with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=10) as health:
                        if health.status != 200:
                            raise RuntimeError('The synthetic server is not ready.')
                    env = {
                        **os.environ, 'WP_I_QUALITY_FIXTURE': str(fixture), 'WP_I_QUALITY_OUT': str(output),
                        'TMPDIR': str(browser_tmp),
                        'DEBUG': ','.join(filter(None, (os.environ.get('DEBUG'), 'pw:browser'))),
                    }
                    phase = 'browser_test'
                    with (output / 'browser.local.log').open('wb') as browser_log:
                        process = subprocess.run(
                            ['node', str(here / f'{args.suite}.cjs')], env=env,
                            stdout=browser_log, stderr=subprocess.STDOUT, check=False,
                        )
                finally:
                    if server.poll() is None:
                        server.send_signal(signal.SIGINT)
                    try:
                        server.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
                    if reader is not None and reader.ident is not None:
                        reader.join(timeout=10)
                    server.stdout.close()
    except Exception as error:
        failure = error
        # Do not echo exception strings: they may contain internal paths or fixture details.
        print(f'Quality runner failed: {failure_kind(phase, error)}', file=sys.stderr)
    # ExitStack has released fixture and browser temporary directories by this point.
    return browser_receipt(args.suite, output, process, browser_tmp, scratch, env, here,
                           failure=failure, failed_phase=phase)


if __name__ == '__main__':
    raise SystemExit(main())
