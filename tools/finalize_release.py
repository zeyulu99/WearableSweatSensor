"""Finalize a standalone GitHub portable release without the original lab tree."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core import VERSION

DIST = ROOT / 'dist'
PACKAGE = DIST / f'WearableSweatSensor-{VERSION}'
VERIFY = ROOT / 'verification'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_passed(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    if not report.get('passed'):
        raise RuntimeError(f'Validation did not pass: {path.name}')
    return report


def copy_licenses():
    destination = PACKAGE / 'THIRD_PARTY_LICENSES'
    destination.mkdir(exist_ok=True)
    notices = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        if name.lower() == 'pip':
            continue
        label = dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See license files'
        notices.append(f'{name} {dist.version}\nLicense: {label}\n')
        for entry in dist.files or []:
            parts = str(entry).replace('\\', '/').split('/')
            if not any(p.lower().startswith(('license', 'copying', 'notice')) for p in parts):
                continue
            source = Path(dist.locate_file(entry))
            # Keep paths relative to this distribution; never copy parent traversal.
            if source.is_file() and '..' not in parts:
                target = destination / name / Path(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, destination / 'Python-LICENSE.txt')
    tcl = Path(sys.base_prefix) / 'tcl'
    if tcl.is_dir():
        for source in tcl.rglob('license*'):
            if source.is_file():
                target = destination / 'Tcl-Tk' / source.relative_to(tcl)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    (PACKAGE / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(notices), encoding='utf-8')


def main():
    if not (PACKAGE / 'WearableSweatSensor.exe').is_file():
        raise RuntimeError('Build the portable executable before finalization.')
    VERIFY.mkdir(exist_ok=True)
    unit = read_passed(VERIFY / 'unit-tests.json')
    source_gui = read_passed(VERIFY / 'source-gui.json')
    if not unit.get('tests_run') or not source_gui.get('checks'):
        raise RuntimeError('Validation reports are empty.')

    for source, target in [
        (ROOT / 'README_PORTABLE.md', PACKAGE / 'README.md'),
        (ROOT / 'CHANGELOG.md', PACKAGE / 'CHANGELOG.md'),
        (ROOT / 'LICENSE_STATUS.md', PACKAGE / 'LICENSE_STATUS.md'),
        (ROOT / 'requirements-lock.txt', PACKAGE / 'DEPENDENCIES.txt'),
        (ROOT / 'docs' / 'Quickstart-EN.pdf', PACKAGE / 'Quickstart-EN.pdf'),
        (ROOT / 'docs' / 'RELEASE_NOTES.md', PACKAGE / 'RELEASE_NOTES.md'),
    ]:
        shutil.copy2(source, target)
    copy_licenses()

    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith('PYTHON') or key.upper() in {'VIRTUAL_ENV', 'CONDA_PREFIX'}:
            env.pop(key, None)
    env['PATH'] = ''
    report_path = VERIFY / 'packaged-gui.json'
    with tempfile.TemporaryDirectory(prefix='sweat-frozen-check-') as home:
        env['SWEAT_SENSOR_HOME'] = home
        completed = subprocess.run(
            [str(PACKAGE / 'WearableSweatSensor.exe'), '--self-test', '--report', str(report_path)],
            env=env, cwd=PACKAGE, timeout=90)
        if completed.returncode != 0:
            raise RuntimeError(f'Frozen GUI acceptance failed: {completed.returncode}')
    frozen = read_passed(report_path)
    if not frozen.get('checks') or frozen.get('version') != VERSION:
        raise RuntimeError('Frozen report is empty or has the wrong version.')
    shutil.copy2(report_path, PACKAGE / 'SIMULATION_TEST_REPORT.json')

    resource = PACKAGE / '_internal' / 'resources' / 'default_devices.csv'
    import csv
    with resource.open(encoding='utf-8-sig', newline='') as handle:
        if list(csv.DictReader(handle)):
            raise RuntimeError('The public build must not include laboratory device addresses.')

    summary = {
        'version': VERSION,
        'distribution': 'GitHub laboratory preview with English manual',
        'unit_tests': unit,
        'source_gui_checks': len(source_gui['checks']),
        'source_gui_passed': source_gui['passed'],
        'packaged_gui_checks': len(frozen['checks']),
        'packaged_gui_passed': frozen['passed'],
        'public_default_device_count': 0,
        'manual': 'Quickstart-EN.pdf',
        'hardware_verified': False,
        'clean_windows_verified': False,
        'code_signed': False,
        'execution_test': 'Frozen EXE launched with empty PATH and no inherited Python variables',
        'build_python': sys.version.split()[0],
    }
    write_json(PACKAGE / 'VALIDATION_SUMMARY.json', summary)
    write_json(DIST / 'VALIDATION_SUMMARY.json', summary)
    hashes = {p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(PACKAGE.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.json'}
    write_json(PACKAGE / 'SHA256SUMS.json', hashes)
    archive = Path(shutil.make_archive(
        str(DIST / f'WearableSweatSensor-{VERSION}-win64-with-manual'), 'zip',
        root_dir=DIST, base_dir=PACKAGE.name))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise RuntimeError('Portable ZIP failed its CRC check.')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(json.dumps({'archive': archive.name, 'bytes': archive.stat().st_size,
                      'sha256': digest, **summary}, indent=2))


if __name__ == '__main__':
    main()
