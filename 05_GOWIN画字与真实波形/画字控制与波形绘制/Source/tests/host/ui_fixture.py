"""Explicit offline-only real-Tk QA fixture; it has no SerialOwner instance."""
from pathlib import Path
import argparse
from collections import deque
import hashlib
import json
import math
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'))
from app import App
from client import HostState
from protocol import DEFAULT_GAINS,FIRMWARE,SCHEMA

class ForbiddenSerialOwner:
    def __init__(self,*args,**kwargs):raise AssertionError('UI QA forbids SerialOwner construction and COM access')

class FixtureOwner:
    def __init__(self,variant):
        from test_host import tlm,diag
        from protocol import decode
        self.state=HostState(variant);self.commands=[]
        self.state.connected=True;self.state.authenticated=True;self.state.stored=DEFAULT_GAINS;self.state.independent=DEFAULT_GAINS
        self.state.stored_revision=12;self.state.independent_revision=12
        item=decode(tlm(1001,variant));item.update(wide_encoder=3250,wide_target=4160,position_error=-910,mode=3,physical_encoder_raw=3375,wide_physical_encoder=3375,actual_applied_q15=-1250,requested_moto=275,mean_adc12=3087,angle_error=2,angle_diff=-1,control_epoch=1001,position_component_epoch=1000,stored_revision=12,active_revision=12,gesture_event_seq=4321,gesture_errors=3,action_reason=4)
        item.update(flags=0x77 if variant==3 else 0x17,motor_permitted_at_completion=True,
                    adaptive_enabled_at_launch=variant==3,adaptive_used_at_launch=variant==3)
        if variant==3:
            import gain_average
            item.update(learning_revision=0,center_p_offset=0,center_d_offset=0)
            for n in range(1001):
                sample=dict(item,control_epoch=n+1,board_ms=5+n*5,
                    active_gains=(48.6171875+(-.3828125 if n%2==0 else .3828125),0,
                                  254.921875+(-3.921875 if n%2==0 else 3.921875),25,0,610))
                gain_average.observe(self.state.gain_average,sample,time.monotonic())
            item['active_gains']=sample['active_gains']
        self.state.latest=item
        self.state.last_control=dict(item);self.state.last_control_rx=time.monotonic();self.state.latest_rx=time.monotonic()
        diagnostic=decode(diag(21,variant));self.state.latest_diagnostic=diagnostic;self.state.last_diagnostic_rx=time.monotonic()
        self.state.learning_events.append(diagnostic);self.state.adoption_events.append(diagnostic)
        self.state.control_gain_events.append(dict(before=DEFAULT_GAINS,after=diagnostic['adoption_after_gains'],
            control_epoch=995,board_ms=123425,freeze=10,adaptive_used=True,motor_permitted=True))
        self.state.learning_event_gaps=3;self.state.adoption_event_gaps=1
        self.state.error='离线QA静态数据：没有连接设备，按钮仅记录测试请求'
        self.state.log_error='离线注入：日志共享额度已达到，非STOP变更已锁定'
        self.state.log_status='离线QA：200Hz记录测试样例（不是实物结果）'
        self.state.max_heartbeat_gap=0.103009
        self.state.motion_reference_start=3120;self.state.motion_reference_start_epoch=1
        self.state.history=deque(((n,1.8*math.exp(-n/450)*math.sin(n/20),0,
            70*math.exp(-n/500)*math.cos(n/31),
            3120+min(n/600,1)*1040+70*math.exp(-n/500)*math.cos(n/31),
            3120+min(n/600,1)*1040,n*5) for n in range(4000 if variant==2 else 1000)),maxlen=4000 if variant==2 else 2000)
    def command(self,*args):self.commands.append(args)
    def set_gains(self,*args):self.commands.append(('SET_GAINS',args))
    def set_kit(self,*args):self.commands.append(('KIT',args))
    def stop(self,*args,**kwargs):pass

def geometry(root):
    result=[];bounds=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_width(),root.winfo_height())
    def walk(widget):
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                x,y,w,h=child.winfo_rootx(),child.winfo_rooty(),child.winfo_width(),child.winfo_height()
                text=str(child.cget('text')) if isinstance(child,(ttk.Label,ttk.Button,tk.Button)) else ''
                clipped=x<bounds[0] or y<bounds[1] or x+w>bounds[0]+bounds[2]+1 or y+h>bounds[1]+bounds[3]+1
                if isinstance(child,(ttk.Label,ttk.Button,tk.Button,ttk.Entry)):
                    result.append({'widget':str(child),'class':child.winfo_class(),'text':text,'bounds':[x-bounds[0],y-bounds[1],w,h],'requested':[child.winfo_reqwidth(),child.winfo_reqheight()],'clipped_window':clipped,'shrunk':w+1<child.winfo_reqwidth() or h+1<child.winfo_reqheight()})
                walk(child)
    walk(root)
    main=root.winfo_children()[0]
    missing=[];main_layout=[]
    for child in main.winfo_children():
        main_layout.append({'widget':str(child),'class':child.winfo_class(),'mapped':bool(child.winfo_ismapped()),'actual':[child.winfo_x(),child.winfo_y(),child.winfo_width(),child.winfo_height()],'requested':[child.winfo_reqwidth(),child.winfo_reqheight()],'pack':child.pack_info() if child.winfo_manager()=='pack' else {}})
        if child.winfo_manager()=='pack' and not child.winfo_ismapped():
            missing.append({'widget':str(child),'class':child.winfo_class(),'text':str(child.cget('text')) if isinstance(child,ttk.Label) else '', 'requested':[child.winfo_reqwidth(),child.winfo_reqheight()]})
    # pack_info's Tcl widget object is represented as an explicit path.
    for item in main_layout:
        if 'in' in item['pack']:item['pack']['in']=str(item['pack']['in'])
    return {'window':bounds,'screen':[root.winfo_screenwidth(),root.winfo_screenheight()],'tk_scaling':float(root.tk.call('tk','scaling')),'controls':result,'clipped':[r for r in result if r['clipped_window'] or r['shrunk']],'unmapped_main_pack':missing,'main_layout':main_layout}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--variant',type=int,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--auto',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);control=args.output/'CONTROL.json'
    import app as app_module
    app_module.SerialOwner=ForbiddenSerialOwner
    root=tk.Tk();callback_errors=[]
    root.report_callback_exception=lambda kind,error,trace:callback_errors.append({'type':kind.__name__,'error':str(error)})
    app=App(root,args.variant);root.title(f'Forest D1 V{args.variant} — OFFLINE QA FIXTURE ONLY')
    app.owner=FixtureOwner(args.variant)
    base_latest=dict(app.owner.state.latest);base_diagnostic=dict(app.owner.state.latest_diagnostic)
    def close_fixture():
        app.closing=True
        for job in root.tk.call('after','info'):root.after_cancel(job)
        root.destroy()
    for variable in app.drafts:variable.set('49.1234567')
    for key in app.swing_reads:
        field=next(x for x in app.schema['fields'] if x['id']==key[1]);app.owner.state.config_reads[key]=(field['defaults'][key[0]],19);app.owner.state.config_saved[key]=19
    for field in range(1,5):app.owner.state.config_reads[(254,field)]=(1,20)
    for field,value in ((5,1),(6,1040),(7,1040)):app.owner.state.config_reads[(254,field)]=(value,21)
    tabs=[child for child in root.winfo_children()[0].winfo_children() if isinstance(child,ttk.Notebook)]
    assert len(tabs)==1;notebook=tabs[0]
    (args.output/'READY.json').write_text(json.dumps({'pid':os.getpid(),'variant':args.variant,'no_serial_owner_instance':True,'title':root.title(),'source_sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'host').glob('*.py')}}),encoding='utf8')
    previous=None;reports=[]
    def poll():
        nonlocal previous
        if control.exists():
            try:
                data=json.loads(control.read_text(encoding='utf8'))
                if data!=previous:
                    previous=data
                    if data.get('close'):close_fixture();return
                    if 'size' in data:root.geometry(f"{data['size'][0]}x{data['size'][1]}+30+30")
                    if 'tab' in data:notebook.select(data['tab'])
                    if 'profile' in data and app.swing_tabs:app.swing_tabs.select(data['profile'])
                    app.owner.state.latest=dict(base_latest)
                    app.owner.state.latest_diagnostic=dict(base_diagnostic)
                    if args.variant in (1,2) and data.get('tab')==1:
                        app.owner.state.latest.update(mode=5,swing_profile=data.get('profile',0),
                            swing_stage=10 if data.get('size',[0])[0]==1180 else 40,actual_applied_q15=0)
                    if args.variant==2 and data.get('tab')==2:
                        app.owner.state.latest.update(motion_state=4,wide_target=4160,wide_encoder=4172,position_error=12,flags=0x17)
                        app.owner.state.latest_diagnostic.update(trajectory_done=True,tracking_settled=False,
                            reference_velocity=0,tracking_error=12,source_reason=5,swing_failure_reason=1)
                    if data.get('fresh',True):
                        app.owner.state.last_control_rx=time.monotonic();app.owner.state.last_diagnostic_rx=time.monotonic()
                        if args.variant==3:app.owner.state.gain_average['last_rx']=time.monotonic()
                    else:app.owner.state.last_control_rx=time.monotonic()-2
                    def report(attempt=0):
                        refresh_start=time.perf_counter();app.refresh();refresh_seconds=time.perf_counter()-refresh_start;root.update_idletasks()
                        if root.winfo_children()[0].winfo_width()!=root.winfo_width() and attempt<5:
                            root.after(25,lambda:report(attempt+1));return
                        record=geometry(root);record.update(variant=args.variant,name=data.get('name','snapshot'),requested_size=data.get('size'),minimum_size=list(root.minsize()),drafts=[v.get() for v in app.drafts],commands=repr(app.owner.commands),no_hardware=True,callback_errors=list(callback_errors),plot_mapped=bool(app.canvas.winfo_ismapped()),plot_size=[app.canvas.winfo_width(),app.canvas.winfo_height()],plot_item_count=len(app.canvas.find_all()),summary_text=app.summary.get(),motion_text=app.motion_detail.get() if args.variant==2 else None,average_text=app.adapt_average.get() if args.variant==3 else None,history_points=len(app.owner.state.history),refresh_seconds=refresh_seconds)
                        record['window_manager_settle_retries']=attempt
                        (args.output/(data.get('name','snapshot')+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
                        reports.append(data.get('name','snapshot')+'.json')
                    root.after(250,report)
            except (OSError,ValueError):pass
        root.after(50,poll)
    if args.auto:
        scenes=[]
        minimum=root.minsize()
        for width,height in ((1180,960),(1400,960),(1000,740)):
            for tab_id in range(len(notebook.tabs())):
                profiles=range(3 if args.variant==1 else 1) if tab_id==1 and app.swing_tabs else (None,)
                for profile_id in profiles:
                    fresh=width!=1400 if args.variant==3 and tab_id==1 else tab_id%2==0
                    scene={'name':f'V{args.variant}_{width}x{height}_tab{tab_id}'+(f'_profile{profile_id}' if profile_id is not None else ''),'size':[width,height],'tab':tab_id,'fresh':fresh}
                    if profile_id is not None:scene['profile']=profile_id
                    scenes.append(scene)
        scene_index=0
        def advance():
            nonlocal scene_index
            if scene_index>=len(scenes):close_fixture();return
            control.write_text(json.dumps(scenes[scene_index]),encoding='utf8');scene_index+=1
            root.after(500,advance)
        root.after(150,advance)
    root.after(100,poll);root.after(30000 if args.auto else 120000,close_fixture);root.mainloop()
    if args.auto:
        records=[json.loads((args.output/name).read_text(encoding='utf8')) for name in reports]
        result={'status':'PASS' if len(records)==len(scenes) and not callback_errors and all(not record['clipped'] and not record['unmapped_main_pack'] and record['plot_mapped'] and record['plot_item_count']>=10 and record['plot_size'][1]>=80 and record['drafts']==['49.1234567']*6 and record['commands']=='[]' for record in records) else 'FAIL','variant':args.variant,'callback_errors':callback_errors,'scenes_expected':len(scenes),'scenes_completed':len(records),'geometry_files':reports,'source_sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in list((ROOT/'host').glob('*.py'))+[Path(__file__)]},'real_Tk_windows':True,'mock_static_data':True,'no_serial_owner_instance':True,'serial_constructor_forbidden':True,'screenshots_obtained':False,'screenshot_limitation':'Bounded real-Tk geometry QA with fresh/stale mocked controls; no screenshot or real device access.'}
        (args.output/'UI_VARIANT_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'variant':args.variant,'status':result['status'],'scenes':len(records),'clipped':sum(len(record['clipped']) for record in records)},ensure_ascii=False))

if __name__=='__main__':main()
