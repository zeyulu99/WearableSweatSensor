"""Run the simulation suite and save a machine-readable count/status."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'tests_run': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped),
              'passed': result.wasSuccessful(), 'mode': 'simulated; no physical BLE hardware'}
    out = ROOT / 'verification' / 'unit-tests.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    sys.exit(0 if report['passed'] else 1)
