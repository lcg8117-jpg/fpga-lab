"""SP24 event, cold-read and explicit-preset checks; no COM is opened."""
import copy
import struct
import unittest
from test_tune_host import Fixture
from tune_presets import load_presets, validate_preset, PRESET_ROOT
from tune_parameters import validate
from swing_tune_host import gesture_description, gesture_runtime_description, GESTURE_REASONS


class SP24Tests(unittest.TestCase):
    def test_event_count_is_independent_of_poll_and_duplicates(self):
        f=Fixture()
        event=lambda count,reason:struct.pack('<IIBBBBI',count,12345,2,2,reason,0x7b,count)
        f.model._status_page(8,event(7,12),f.now)
        row=f.model.telemetry['gesture_event']
        self.assertFalse(row['new_since_poll'])
        self.assertEqual(row['event_ms'],12345)
        f.model._status_page(8,event(7,7),f.now)
        self.assertFalse(f.model.telemetry['gesture_event']['new_since_poll'])
        self.assertIn('已接受',gesture_description(f.model.telemetry['gesture_event']))
        f.model._status_page(8,event(8,8),f.now)
        self.assertTrue(f.model.telemetry['gesture_event']['new_since_poll'])
        f.model._status_page(8,event(8,8),f.now)
        self.assertFalse(f.model.telemetry['gesture_event']['new_since_poll'])
        self.assertEqual(f.model.telemetry['gesture_event']['event_count'],8)

    def test_every_requested_reason_has_readable_label(self):
        self.assertEqual(set(GESTURE_REASONS),set(range(13)))
        for reason in range(13):
            self.assertIn(GESTURE_REASONS[reason],gesture_description(dict(
                event_count=1,event_ms=44,code=3,action=1,reason=reason,event_flags=1,event_id=1)))
        self.assertIn('轮询次数不代表',gesture_description(None))

    def test_runtime_flags_timestamp_and_poll_decode(self):
        f=Fixture()
        f.model._status_page(9,struct.pack('<IIHBBBBBB',987,45678,13,4,255,40,3,6,255),f.now)
        row=f.model.telemetry['gesture_runtime']
        self.assertEqual((row['poll_sequence'],row['timestamp_ms'],row['error_count'],row['error']),(987,45678,13,4))
        for name in ('ready','enabled','blocked','fresh_balance_ready','motor','zero','estop','fault',
                     'swing_active','handoff','auto_armed','manual_run','deadman','hold_initialized','rotation_active','busy'):
            self.assertTrue(row[name],name)
        text=gesture_runtime_description(row)
        for label in ('传感器就绪','当前错误','手势屏蔽','交接请求','HOLD初始化','查询序号'):
            self.assertIn(label,text)

    def test_cold_zero_e_ordinary_reads_do_not_block_inventory(self):
        f=Fixture()
        for pid in range(0x40,0x4a):
            f.model._issue(0x71,bytes([pid]),'read',param=pid)
            f.answer(struct.pack('<BI',pid,0))
        from tune_parameters import BY_ID
        for pid in range(0x40,0x4a):
            self.assertEqual(f.model.parameters[BY_ID[pid][1]]['value'],0)
            with self.assertRaises(ValueError):validate(BY_ID[pid][1],0)
        self.assertNotIn('超出',f.model.note)

    def test_e_verify_zero_is_still_rejected(self):
        f=Fixture()
        f.model.submit('one','set','error_node_1',1)
        f.answer()
        f.answer(struct.pack('<BI',0x40,0))
        self.assertEqual(f.model.requests['one']['state'],'indeterminate')
        self.assertNotEqual(f.model.parameters['error_node_1']['value'],1)

    def test_all_six_presets_write_only_config_with_independent_readback(self):
        presets=load_presets()
        self.assertEqual(len(presets),6)
        for preset in presets:
            with self.subTest(preset=preset['id']):
                f=Fixture()
                f.model.submit('preset','apply-preset',value=preset)
                for _ in range(300):
                    if f.model.requests['preset']['state']=='verified':break
                    if f.model.pending:f.answer()
                    else:f.tick()
                self.assertEqual(f.model.requests['preset']['state'],'verified')
                writes=[p for p,_,_ in f.sent if p[18]==0x70]
                self.assertEqual(len(writes),len(preset['parameters']))
                self.assertEqual(set(f.model.requests['preset']['confirmed']),set(preset['parameters']))
                self.assertFalse(any(p[18] in (2,3,5) for p,_,_ in f.sent))
                for field,value in preset['parameters'].items():
                    self.assertEqual(f.model.parameters[field]['value'],value)

    def test_angle_candidates_change_one_gain_from_field_baseline(self):
        presets={p['id']:p for p in load_presets()}
        baseline=presets['field_baseline']['parameters']
        for name,field,number in (('angle_d80','angle_kd',80),('angle_i780','angle_ki',780)):
            with self.subTest(preset=name):
                candidate=presets[name]
                params=candidate['parameters']
                self.assertEqual(set(params),set(baseline))
                self.assertEqual({key:value for key,value in params.items() if value!=baseline[key]}, {field:number})
                self.assertEqual((params['position_kp'],params['position_ki'],params['position_kd']), (112,0,48))
                self.assertEqual(params['adaptive_mode'],0)
                self.assertNotIn('adaptive_profile',params)
                self.assertTrue(candidate['no_RUN_ZERO_or_profile_commit'])
                self.assertIn('engineering',candidate['evidence_grade'])
                self.assertEqual(candidate['expected_readonly'],presets['field_baseline']['expected_readonly'])

    def test_presets_reject_moving_state_readonly_mismatch_and_old_identity(self):
        preset=load_presets()[0]
        for flags in ({'pwm':1},{'swing_active':True},{'run_session':True}):
            f=Fixture();f.model.status.update(flags)
            self.assertEqual(f.model.submit('p','apply-preset',value=preset)['state'],'rejected')
            self.assertFalse(any(p[18]==0x70 for p,_,_ in f.sent))
        f=Fixture();f.model.parameters['encoder_mode']['value']=0
        self.assertEqual(f.model.submit('p','apply-preset',value=preset)['state'],'rejected')
        bad=copy.deepcopy(preset);bad['firmware_uid_hex']='53503232'
        with self.assertRaises(ValueError):validate_preset(bad)
        bad=copy.deepcopy(preset);bad['parameters']['adaptive_mode']=1
        with self.assertRaises(ValueError):validate_preset(bad)

    def test_stop_cancels_remaining_preset_without_replay(self):
        f=Fixture();f.model.submit('p','apply-preset',value=load_presets()[0])
        f.tick();f.answer();f.answer()
        f.model.submit('s','stop');f.answer()
        for _ in range(20):
            if f.model.pending:f.answer()
            else:f.tick()
        self.assertEqual(f.model.requests['p']['state'],'cancelled')
        self.assertIsNone(f.model.batch)
        self.assertFalse(any(p[18] in (2,3,5) for p,_,_ in f.sent))

    def test_event_runtime_polling_is_interleaved_with_other_readonly_pages(self):
        f=Fixture();pages=[]
        for _ in range(100):
            f.tick()
            if f.model.pending:
                if f.model.pending['op']==1:pages.append(f.model.pending['payload'][0])
                f.answer()
        self.assertTrue({0,1,2,3,4,5,6,7,8,9}.issubset(pages))
        self.assertGreater(pages.count(8),pages.count(4))
        self.assertGreater(pages.count(9),pages.count(4))
        self.assertFalse(any(p[18] in (2,3,5,0x70) for p,_,_ in f.sent))

    def test_bundled_e_engineering_profile_cannot_be_control_qualified(self):
        import json
        profile=json.loads((PRESET_ROOT/'e_engineering_shadow.json').read_text(encoding='utf-8'))
        f=Fixture()
        self.assertEqual(f.model.submit('e','load-profile',value=dict(profile=profile,control=True))['state'],'rejected')
        self.assertFalse(any(p[18]==0x70 for p,_,_ in f.sent))

    def test_bundled_e_shadow_import_matches_words_and_stays_off(self):
        import json
        from tune_parameters import BY_NAME
        profile=json.loads((PRESET_ROOT/'e_engineering_shadow.json').read_text(encoding='utf-8'))
        compiled=json.loads((PRESET_ROOT/'e_engineering_shadow_compiled.json').read_text(encoding='utf-8'))
        f=Fixture();f.model.submit('e','load-profile',value=dict(profile=profile,control=False))
        for _ in range(300):
            if f.model.requests['e']['state']=='verified':break
            if f.model.pending:f.answer()
            else:f.tick()
        self.assertEqual(f.model.requests['e']['state'],'verified')
        self.assertEqual(f.values[0x31],0)
        self.assertEqual(f.values[0x33],2)
        self.assertEqual(compiled['adaptive_mode_after_explicit_import'],0)
        for name,expected in compiled['profile_fields_u32'].items():
            self.assertEqual(f.values[BY_NAME[name][0]],expected)
        self.assertFalse(any(p[18] in (2,3,5) for p,_,_ in f.sent))


if __name__=='__main__':unittest.main()
