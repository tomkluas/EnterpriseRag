"""Start the real packaged app on an isolated port, verify HTTP, then stop its tree."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    report = {'executable': str(executable), 'port': port, 'checks': []}
    log_path = root / 'data/logs/packaged-http-check.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(executable), '--port', str(port), '--no-browser'], cwd=executable.parent,
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        started = time.monotonic()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=1) as client:
                while time.monotonic() - started < 40:
                    if process.poll() is not None:
                        raise RuntimeError(f'Packaged app exited: 0x{process.returncode & 0xffffffff:08X}; see {log_path}')
                    try:
                        response = client.get('/api/health')
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError(f'Packaged app startup timed out; see {log_path}')
                report['startup_seconds'] = round(time.monotonic() - started, 3)
                for route in ['/api/health', '/', '/login', '/knowledge']:
                    response = client.get(route)
                    assert response.status_code == 200, (route, response.status_code)
                    if route == '/api/health':
                        assert response.json() == {'status': 'ok'}
                    else:
                        assert 'id="app"' in response.text
                    report['checks'].append({'path': route, 'status': response.status_code, 'pass': True})
                report['running_before_cleanup'] = process.poll() is None
        finally:
            # PyInstaller onefile 有父引导进程和子进程，只关闭本次启动的进程树。
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            process.wait(timeout=10)
    report_path = root / 'docs/testing/packaged-verification-2026-10-30.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
