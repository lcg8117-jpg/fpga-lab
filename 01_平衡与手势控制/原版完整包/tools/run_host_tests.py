"""Run FakeTransport tests without opening hardware."""
from pathlib import Path
import unittest,sys,io,json,hashlib
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r));stream=io.StringIO()
suite=unittest.defaultTestLoader.discover(str(r/'tests'),pattern='test_host.py')
result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
(r/'reports/host_tests.log').write_text(stream.getvalue(),'utf-8')
record=dict(passed=result.wasSuccessful(),tests=result.testsRun,source_sha256={
    p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (r/'host').glob('*.py')},hardware_actions=0)
(r/'reports/HOST_TESTS.json').write_text(json.dumps(record,indent=2)+'\n','utf-8')
assert result.wasSuccessful(),stream.getvalue()
print('PASS18 FakeTransport tests; no hardware actions')
