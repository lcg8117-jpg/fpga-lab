"""G3 live trial: visible manual controls and GOWIN playback."""
from pathlib import Path
import argparse,json,os,time,tkinter as tk
from tkinter import ttk
from client import SerialOwner
from protocol import ZERO,RUN,STOP,CLEAR,FIRMWARE
from trace_log import TraceLogger
from gowin import GowinController
from gowin_widget import GowinWidget

HERE=Path(__file__).resolve().parent;DEPLOY=HERE.parent
MODES={0:'待机：扶直后点击校零',1:'已校零：点击开始平衡',2:'等待平衡准入',3:'正在平衡',4:'故障：停机后清除故障'}

def current_status(snapshot):
 # Before ZERO the board supplies valid status telemetry, but no completed
 # balance transaction. This is only for manual startup and status display;
 # GowinController keeps its original coherent-control requirement for PLAY.
 row=snapshot.get('latest') or snapshot.get('last_control') or {}
 stamp=snapshot.get('latest_rx') if snapshot.get('latest') else snapshot.get('last_control_rx')
 fresh=bool(snapshot.get('connected') and snapshot.get('authenticated') and
            stamp and 0 <= time.monotonic()-stamp <= .5)
 return row,fresh

class LiveWindow:
 def __init__(self,root,owner,port):
  self.root=root;self.owner=owner;self.port=port;self.last_keys={};self.closed=False
  self.controller=GowinController(owner,export_directory=DEPLOY/'records')
  root.title('GOWIN G3 · C1运动候选（尚未实机验收）');root.geometry('1250x700');root.minsize(900,550)
  self.device=tk.StringVar(value='正在连接真实设备，尚未运行')
  ttk.Label(root,textvariable=self.device,font=('Microsoft YaHei UI',12)).pack(fill='x',padx=12,pady=(10,5))
  ttk.Label(root,text='扶直摆杆 → 点击“校零” → 显示已校零后点击“开始平衡” → 稳定后点击“播放运动图像”。',wraplength=1200).pack(fill='x',padx=12)
  controls=ttk.Frame(root);controls.pack(fill='x',padx=12,pady=(10,2))
  self.control_buttons={}
  for key,label in [('z','校零'),('r','开始平衡'),('escape','停机'),('c','清除故障')]:
   button=ttk.Button(controls,text=label,width=15,command=lambda action=key:self.manual_action(action,'BUTTON'))
   button.pack(side='left',padx=(0,10));self.control_buttons[key]=button
  ttk.Label(controls,text='快捷键仍可用：Z / R / Esc / C',foreground='#555').pack(side='left',padx=6)
  self.widget=GowinWidget(root,self.controller);self.widget.pack(fill='both',expand=True)
  ttk.Label(root,text='沿用原轨迹：42秒先行运动，随后按 G→O→W→I→N 显示；完整轨迹80秒。',foreground='#555').pack(fill='x',padx=12,pady=(0,8))
  root.bind('<KeyPress>',self.key);root.protocol('WM_DELETE_WINDOW',self.close)
  self.status_id=root.after(200,self.status)
  root.after_idle(root.focus_set)
 def key(self,event):
  key=event.keysym.lower()
  if key not in ('z','r','escape','c'):return
  return self.manual_action(key,'KEY')
 def manual_action(self,key,source):
  now=time.monotonic()
  if key!='escape' and now-self.last_keys.get(key,0)<1:return 'break'
  self.last_keys[key]=now;s=self.owner.state.snapshot();row,fresh=current_status(s)
  try:
   if key=='escape':
    self.controller.cancel.set();self.owner.command(STOP,source='G3_MANUAL_'+source+'_ESC')
   else:
    if not fresh:
     raise RuntimeError('等待已连接设备的新采样')
    if row.get('firmware_id')!=FIRMWARE[5]:raise RuntimeError('不是G3配套固件')
    if row.get('motor_permitted_at_completion') or row.get('mode') not in (0,1,4) or row.get('actual_applied_q15')!=0:
     raise RuntimeError('当前正在平衡；重新校零或清故障前先点击停机')
    if key=='r':
     if row.get('mode')!=1 or row.get('fault'):raise RuntimeError('先扶直并点击校零，等到已校零后再点击开始平衡')
     opcode=RUN
    elif key=='z':
     if row.get('fault'):raise RuntimeError('先点击清除故障，再扶直并点击校零')
     opcode=ZERO
    else:opcode=CLEAR
    self.owner.command(opcode,source='G3_MANUAL_'+source+'_'+key.upper())
   self.controller.status={'z':'已手动请求校零，请保持扶直','r':'已手动请求平衡，请等待状态更新','escape':'已手动请求停机','c':'已手动请求清除故障'}[key]
  except Exception as exc:self.controller.status=str(exc)
  return 'break'
 def status(self):
  s=self.owner.state.snapshot();row,fresh=current_status(s);diag=s.get('latest_diagnostic') or {}
  error=s.get('error') or s.get('log_error')
  if error:line='连接/记录报错：'+str(error)
  elif not s.get('authenticated'):line='正在连接 '+self.port+'，等待G3身份确认'
  else:
   line=f"{self.port} · G3 56354733 · {MODES.get(row.get('mode'),'等待状态')} · 故障 {row.get('fault','?')} · PWM {row.get('actual_applied_q15','?')} · ENC {row.get('wide_encoder','?')}"
   if not fresh:line+=' · 等待新采样'
  self.device.set(line)
  proof=dict(unix=time.time(),pid=os.getpid(),port=self.port,mapped=bool(self.root.winfo_ismapped()),
   title=self.root.title(),button_text=self.widget.play_button.cget('text'),ui_revision='G3_VISIBLE_CONTROLS_R1',
   control_buttons=[button.cget('text') for button in self.control_buttons.values()],
   connected=s.get('connected'),authenticated=s.get('authenticated'),control_fresh=s.get('control_fresh'),status_fresh=fresh,
   crc_errors=s.get('crc_errors'),sequence_gaps=s.get('sequence_gaps'),timeouts=s.get('timeouts'),pending_mutations=s.get('pending_mutations'),
   error=error,mode=row.get('mode'),fault=row.get('fault'),motor_permitted=row.get('motor_permitted_at_completion'),
   pwm=row.get('actual_applied_q15'),firmware_id=row.get('firmware_id'),schema=row.get('schema'),
   board_ms=row.get('board_ms'),epoch=row.get('control_epoch'),sequence=row.get('sequence'),
   pid_gains=row.get('manual_gains'),config_revision=row.get('config_revision'),zero_valid=diag.get('zero_valid'),
   controller_status=self.controller.status,canvas_size=[self.widget.canvas.winfo_width(),self.widget.canvas.winfo_height()],
   mapped_points=len(self.widget.points),busy=self.controller.busy,
   raw_log=str(getattr(self.owner.logger,'raw_path','')),events_log=str(getattr(self.owner.logger,'events_path','')))
  dest=DEPLOY/'LIVE_HOST_STATE.json';tmp=dest.with_suffix('.tmp')
  tmp.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(dest)
  self.status_id=self.root.after(500,self.status)
 def close(self):
  if self.closed:return
  self.closed=True;self.root.after_cancel(self.status_id);self.widget.destroy()
  self.owner.stop();self.root.destroy()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--port',required=True);parser.add_argument('--verify-prior-upload',action='store_true');args=parser.parse_args()
 try:
  import ctypes
  ctypes.windll.shcore.SetProcessDpiAwareness(1)
 except Exception:pass
 owner=SerialOwner(5,args.port,DEPLOY/'logs',logger_factory=TraceLogger)
 if args.verify_prior_upload:
  from prior_upload import GUARD,check_prior
  owner.state.error=GUARD
 owner.start()
 if args.verify_prior_upload:check_prior(owner,DEPLOY/'UI_Upload_Fix_R1/FAILURE_ANALYSIS.json',DEPLOY/'UI_Upload_Fix_R1/PRIOR_UNKNOWN_RESOLUTION.json')
 root=tk.Tk();LiveWindow(root,owner,args.port);root.mainloop()

if __name__=='__main__':main()
