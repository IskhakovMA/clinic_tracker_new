"""Exercise pip's real offline integrity boundary using a synthetic wheel."""
import hashlib
import subprocess
import sys
import zipfile

import pytest


@pytest.mark.parametrize('fault', ['none', 'tampered', 'missing_hash', 'wrong_version'])
def test_offline_wheel_install_requires_approved_version_and_hash(tmp_path, fault):
    wheel = tmp_path / 'clinic_synthetic_integrity-1.0-py3-none-any.whl'
    info = 'clinic_synthetic_integrity-1.0.dist-info/'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(info + 'METADATA', 'Metadata-Version: 2.1\nName: clinic-synthetic-integrity\nVersion: 1.0\n')
        archive.writestr(info + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info + 'RECORD', '')
    approved = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if fault == 'tampered':
        with zipfile.ZipFile(wheel, 'a') as archive:
            archive.writestr('synthetic.txt', 'changed after approval')
    version = '2.0' if fault == 'wrong_version' else '1.0'
    requirement = f'clinic-synthetic-integrity=={version}'
    if fault != 'missing_hash':
        requirement += f' --hash=sha256:{approved}'
    lock = tmp_path / 'requirements.txt'
    lock.write_text(requirement + '\n')
    result = subprocess.run([
        sys.executable, '-m', 'pip', 'install', '--no-index', '--find-links', str(tmp_path),
        '--no-deps', '--require-hashes', '--only-binary=:all:', '--disable-pip-version-check',
        '--target', str(tmp_path / 'installed'), '-r', str(lock),
    ], capture_output=True, text=True)
    output = result.stdout + result.stderr
    if fault == 'none':
        assert result.returncode == 0, output
        assert (tmp_path / 'installed' / info / 'METADATA').is_file()
    else:
        assert result.returncode != 0
        assert not (tmp_path / 'installed' / info / 'METADATA').exists()
        assert {'tampered': 'DO NOT MATCH THE HASHES', 'missing_hash': 'Hashes are required',
                'wrong_version': 'No matching distribution found'}[fault] in output
