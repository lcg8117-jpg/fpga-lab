"""V5D1 portable source/native/bitstream tamper negatives; no device access."""
from pathlib import Path
import json,shutil,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools'))
from verify_draw_release import verify
from verify_amplitude_release import inside
class DeliveryGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        record=json.loads((ROOT/'V5/native/RESULT.json').read_text(encoding='utf-8'))
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT.parent);cls.root=Path(cls.temp.name)
        paths={'reports/QUALIFICATION.json','V5/bitstream/J280_V5_Draw_D1.fs','V5/native/RESULT.json','V5/native/SYNTHESIS_GATE.json','reports/rtl/fixed/summary.csv'}
        paths.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'reference').rglob('*') if p.is_file())
        for name in ('DRAW_RTL','SYSTEM_CURRENT','HOST_FINAL','UI_HIDDEN'):
            paths.add(f'reports/{name}.json');proof=json.loads((ROOT/f'reports/{name}.json').read_text(encoding='utf-8'))
            paths.update(proof.get('source_sha256',proof.get('input_sha256')))
        paths.update('tools/'+name for name in ('build_native.py','synthesis_gate.py','read_active_json.py'))
        paths.update('V5/project/'+name for name in record['source_sha256'])
        paths.update('V5/native/'+name for name in record['source_sha256'])
        paths.update('V5/native/'+name for name in record['evidence_sha256'])
        for name in paths:
            target=cls.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target);target.chmod(0o600)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_portable_current_gate(self):self.assertTrue(verify(self.root,False)['accepted'])
    def test_bitstream_source_and_report_tampering_are_rejected(self):
        for name in ('V5/bitstream/J280_V5_Draw_D1.fs','V5/project/src/forest_d1_board.sdc','V5/native/impl/pnr/forest_d1_v5_tr_content.html'):
            path=self.root/name;original=path.read_bytes();path.write_bytes(original+b'TAMPER')
            try:
                with self.assertRaises((ValueError,AssertionError)):verify(self.root,False)
            finally:path.write_bytes(original)
    def test_escape_paths_rejected(self):
        for name in ('../outside','C:/outside','/outside'):
            with self.assertRaises(ValueError):inside(self.root,name)
if __name__=='__main__':unittest.main()
