"""V5 manual-balance controls, using fake owners and no hardware objects."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util, sys, time, unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
from client import SerialOwner
from key_actions import DEFAULT,KeyActions
from protocol import CANCEL_HOLD,POSITION,READ_CONFIG,READ_GAINS,RUN,SELECT_SWING,START_SWING,STATUS,STOP,TRAJECTORY,WRITE_CONFIG,decode

class FakeOwner:
    def __init__(self,variant):self.state=SimpleNamespace(variant=variant);self.calls=[];self.events=[]
    def command(self,*args,**kwargs):self.calls.append((args,kwargs))
    def trace(self,event,**fields):self.events.append((event,fields))

class V5Manual(unittest.TestCase):
    def test_v5_old_swing_keys_are_inert_and_traced_once_per_down_edge(self):
        owner=FakeOwner(5);keys=KeyActions(owner,DEFAULT);keys.arm()
        for key in 'BCDE':
            keys.on_key(ord(key),True);keys.on_key(ord(key),True);keys.on_key(ord(key),False)
        self.assertEqual(owner.calls,[])
        denied=[fields for event,fields in owner.events if event=='key_rejected']
        self.assertEqual([item['action']for item in denied],['START','SWM4','SA21','144'])
        self.assertTrue(all(item['reason']=='swing_removed'for item in denied))

    def test_v5_stop_hold_positions_profiles_and_repress_still_dispatch(self):
        owner=FakeOwner(5);keys=KeyActions(owner,DEFAULT);keys.arm()
        for key in 'AF':keys.on_key(ord(key),True);keys.on_key(ord(key),False)
        keys.set_layer(2)
        for key in 'GLG':keys.on_key(ord(key),True);keys.on_key(ord(key),False)
        keys.set_layer(3)
        for key in 'OP':keys.on_key(ord(key),True);keys.on_key(ord(key),False)
        self.assertEqual([call[0][0]for call in owner.calls],[STOP,CANCEL_HOLD,TRAJECTORY,TRAJECTORY])
        self.assertEqual([call[1]['payload']for call in owner.calls[2:]],[b'\x03',b'\x04'])

    def test_other_swing_variants_keep_first_layer_actions(self):
        for variant in(1,2,4):
            owner=FakeOwner(variant);keys=KeyActions(owner,DEFAULT);keys.arm()
            for key in 'BCDE':keys.on_key(ord(key),True);keys.on_key(ord(key),False)
            self.assertEqual([call[0][0]for call in owner.calls],[START_SWING,SELECT_SWING,SELECT_SWING,SELECT_SWING])
            self.assertFalse(any(event=='key_rejected'for event,fields in owner.events))

    def test_v5_client_rejects_all_swing_entrypoints_without_queueing(self):
        owner=SerialOwner(5,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        rejected=[(START_SWING,0,b''),(SELECT_SWING,0,b'\x00')]
        rejected += [(READ_CONFIG,group,b'\x00')for group in range(3)]
        rejected += [(WRITE_CONFIG,group,b'\x00'+bytes(4))for group in range(3)]
        rejected += [(READ_CONFIG,254,b'\x00'),(WRITE_CONFIG,254,b'\x00'+bytes(4))]
        for opcode,group,payload in rejected:
            with self.assertRaisesRegex(ValueError,'起摆'):owner.command(opcode,group,payload)
            self.assertTrue(owner.queue.empty())

    def test_v5_manual_run_and_non_swing_configuration_remain_available(self):
        owner=SerialOwner(5,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        for opcode,group,payload in((RUN,0,b''),(READ_CONFIG,253,b'\x00'),(READ_CONFIG,254,b'\x05')):owner.command(opcode,group,payload)
        self.assertEqual([owner.queue.get_nowait()[2]for unused in range(3)],[RUN,READ_CONFIG,READ_CONFIG])

    def test_v4_client_keeps_swing_commands_and_configuration(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        for opcode,group,payload in((START_SWING,0,b''),(SELECT_SWING,0,b'\x02'),(READ_CONFIG,2,b'\x00'),(READ_CONFIG,254,b'\x00')):owner.command(opcode,group,payload)
        self.assertEqual(owner.queue.qsize(),4)

    def test_v5_status_bootstrap_only_reads_gain_and_transport_capability(self):
        spec=importlib.util.spec_from_file_location('v5_manual_fixtures',ROOT/'tests/host/test_host.py');fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
        owner=SerialOwner(5,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.pending[1]=(STATUS,0,b'',time.monotonic())
        raw=fixtures.ack(STATUS,1,5);owner._receive(raw,decode(raw))
        self.assertTrue(owner.state.authenticated)
        queued=[owner.queue.get_nowait()for unused in range(owner.queue.qsize())]
        self.assertEqual([(row[2],row[3],row[4])for row in queued],[(READ_GAINS,255,b''),(READ_CONFIG,254,b'\x08')])

    def test_real_tk_v5_has_manual_run_and_motion_without_swing_controls(self):
        import tkinter as tk
        from app import App
        for variant in(5,):
            root=tk.Tk();root.withdraw()
            try:
                with patch('app.choose_log_directory',return_value=ROOT/'unused'):app=App(root,variant)
                def labels(widget):
                    result=[]
                    for child in widget.winfo_children():
                        if child.winfo_class()in('TButton','Button'):result.append(str(child.cget('text')))
                        result.extend(labels(child))
                    return result
                buttons=labels(root)
                self.assertIn('RUN 仅平衡',buttons)
                self.assertEqual(sum('相对'in label for label in buttons),0)
                if variant==5:
                    self.assertIsNone(app.swing_tabs);self.assertEqual(app.swing_values,{})
                    self.assertNotIn('启动板端已选方案',buttons)
                    self.assertIn('分段台阶驻留',buttons);self.assertIn('圆顶驻留往返',buttons)
                    self.assertTrue(hasattr(app,'draw_editor'))
                    self.assertIn('V5已移除自动起摆',app.notice.get())
                else:
                    self.assertIsNotNone(app.swing_tabs);self.assertTrue(app.swing_values)
                    self.assertIn('启动板端已选方案',buttons)
            finally:root.destroy()

if __name__=='__main__':unittest.main()
