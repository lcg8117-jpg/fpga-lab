"""Offline C6 wire contract tests. No serial, motor or plant execution."""
from pathlib import Path
import json
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
sys.path.insert(0,str(Path(__file__).parent))
import draw_trajectory as draw
from word_trajectory import generate
from client import HostState
from protocol import WRITE_CONFIG,READ_CONFIG
from test_draw import FakeOwner


class TrackingContract(unittest.TestCase):
    def test_host_matches_frozen_matlab_input_not_a_different_trajectory(self):
        for word in ('GOWIN','EasyStart'):
            expected=json.loads((ROOT/'examples'/f'Word_{word}.json').read_text(encoding='utf8'))
            actual=generate(word)
            self.assertEqual(actual['points'],expected['points'])
            for key in ('checksum','total_s','pre_motion_s','word_s','window_ms','word_start_ms','word_end_ms','tracking_mode'):
                self.assertEqual(actual['word_metadata'][key],expected['word_metadata'][key])
            for a,b in zip(actual['target_only_xy'],expected['target_only_xy']):
                self.assertAlmostEqual(a[0],b[0],places=10)
                self.assertAlmostEqual(a[1],b[1],places=10)

    def test_mode_zero_crc_is_legacy_and_mode_is_bound_to_commit(self):
        points=draw.prepare([0,5,0]);legacy=draw.SEED^len(points)
        for index,point in enumerate(points):
            legacy=((legacy<<1)|(legacy>>31))&0xffffffff
            legacy^=(index<<16)|(point&0xffff)
        self.assertEqual(draw.checksum(points,0),legacy)
        self.assertNotEqual(draw.checksum(points,1),legacy)
        for value in (True,-1,2,'1'):
            with self.assertRaises(ValueError):draw.checksum(points,value)

    def test_each_upload_sets_and_independently_reads_its_mode(self):
        for mode in (0,1):
            owner=FakeOwner();points=draw.prepare([0,5,0])
            result=draw.upload(owner,points,threading.Event(),tracking_mode=mode)
            writes=[c for c in owner.calls if c[0]==WRITE_CONFIG]
            self.assertEqual(writes[0][2],bytes((7,mode,0,0,0)))
            self.assertEqual(owner.tracking,mode);self.assertEqual(result['tracking_mode'],mode)
            self.assertEqual(result['duration_ms'],(len(points)-3)*100*(mode+1))
            self.assertIn(b'\x07',[c[2]for c in owner.calls if c[0]==READ_CONFIG])

    def test_mismatched_independent_mode_readback_is_rejected(self):
        class WrongMode(FakeOwner):
            def command(self,*args,**kwargs):
                ticket=super().command(*args,**kwargs)
                if args[0]==READ_CONFIG and args[2]==b'\x07':
                    self.state.command_results[ticket]['config_read_value']=0
                return ticket
        with self.assertRaisesRegex(RuntimeError,'独立读回'):
            draw.upload(WrongMode(),draw.prepare([0,5,0]),threading.Event(),tracking_mode=1)

    def test_word_history_covers_the_whole_declared_program(self):
        self.assertGreaterEqual(HostState(5).word_history.maxlen,24000)
        self.assertLessEqual(max(generate(w)['word_metadata']['total_s']for w in ('GOWIN','EasyStart')),102)

    def test_ordinary_file_entry_cannot_silently_play_compensated_word(self):
        import tkinter as tk
        from tkinter import ttk
        from draw_editor import DrawEditor
        root=tk.Tk();root.withdraw()
        try:
            panel=DrawEditor(ttk.Frame(root),lambda:None)
            with tempfile.TemporaryDirectory() as folder:
                file=Path(folder)/'word.json'
                file.write_text(json.dumps(dict(format=draw.FORMAT,sample_ms=100,tracking_mode=1,points=draw.prepare([0,5,0]))),encoding='utf8')
                with patch('draw_editor.filedialog.askopenfilename',return_value=str(file)):
                    panel.load()
            self.assertIn('文字专用上传入口',panel.status.get());self.assertIsNone(panel.uploaded)
            panel.destroy()
        finally:root.destroy()


if __name__=='__main__':unittest.main()
