"""Minimal manual V4 desktop: explicit BLE connect, balance, six positions."""
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from dual_client import DualOwner
from protocol import *
from select_transport import choose_transport,list_usb_choices
from local_settings import (DEFAULT_POSITION_KEYS,DEFAULT_SETTINGS,load_settings,save_settings,
    validate_settings,normalize_position_keys,normalize_usb_port,normalize_usb_baud)
from selected_keyboard import SelectedKeyboard

ROOT=Path(__file__).resolve().parent
NAMES=('角度P','角度I','角度D','位置P','位置I','位置D')
DEFAULT_ADDRESS=DEFAULT_SETTINGS['ble_address']

def enable_dpi_awareness():
    import os
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):ctypes.windll.user32.SetProcessDPIAware()

def telemetry_text(s):
    """Format only current-session compact data. Used by offline QA too."""
    item=s['latest']
    if not item:return '等待板端数据','尚无位置数据','尚无误差数据'
    fresh=s['control_fresh'];scale=360/1040
    state={0:'未校零',1:'已停止',2:'准备中',3:'平衡中',4:'故障停机'}.get(item['mode'],f"状态{item['mode']}")
    status=f"{state} · {'数据有效' if fresh else '等待新控制数据'} · 故障{item['fault']} · 电机许可{'开' if item['flags']&4 else '关'}"
    actual=item['wide_encoder'];reference=item['wide_target'];final=item['final_target']
    position=f"实测 {actual} / {actual*scale:.3f}°    当前参考 {reference} / {reference*scale:.3f}°    最终目标 {final} / {final*scale:.3f}°"
    error=(f"跟踪误差 {actual-reference:+d}计数 / {(actual-reference)*scale:+.3f}°    "
           f"最终误差 {actual-final:+d}计数 / {(actual-final)*scale:+.3f}°    "
           f"摆杆角误差 {item['angle_error']*345/4096:+.3f}°（目标 {item['target_angle_error']*345/4096:+.3f}°）")
    return status,position,error

def identity_text(s,closed=False):
    """Report usable communication without a separate diagnostic-handshake step."""
    connected=bool(s.get('connected')) and not closed
    failed=bool(s.get('error'))
    route='USB串口' if s.get('transport')=='usb' else '蓝牙'
    if connected and failed:state=route+'已连接 · 通信异常'
    elif connected and s.get('authenticated'):state=route+'已连接 · 板端有回包，可操作'
    elif connected:state=route+'已连接 · 等待板端回包'
    elif closed:state=route+'已断开'
    else:state=route+'尚未连接'
    firmware=s.get('observed_firmware');schema=s.get('observed_schema')
    if isinstance(firmware,int) and not isinstance(firmware,bool):state+=f' · 实收FW {firmware:08X}'
    if isinstance(schema,int) and not isinstance(schema,bool):state+=f' / schema {schema:08X}'
    return state

def link_summary(s):
    """Display only measured transport quantities; do not invent loss/interval values."""
    metadata=s.get('connection_metadata') or {}
    tuning=metadata.get('connection_tuning') or {}
    measured=tuning.get('before_disconnect') or tuning.get('after_startup_sync') or tuning.get('after_request') or tuning.get('before') or {}
    mtu=measured.get('mtu_size',metadata.get('mtu_size',metadata.get('mtu')))
    interval=(measured.get('parameters') or {}).get('connection_interval_ms',metadata.get('connection_interval_ms'))
    parts=[f"接收{s.get('rx_bytes',0)}B",f"CRC{s.get('crc_errors',0)}/格式{s.get('format_errors',0)}/超时{s.get('timeouts',0)}",
        'MTU '+(str(mtu) if mtu is not None else '未提供'),
        '间隔 '+(str(interval)+'ms' if interval is not None else '系统未提供')]
    dropped=metadata.get('rx_dropped_bytes',metadata.get('dropped_bytes'))
    if dropped is not None:parts.append(f'接收弃字节{dropped}')
    reason=s.get('diagnostic_capture_error') or s.get('diagnostic_warning') or s.get('diagnostic_last_rejection')
    if reason:parts.append(str(reason).replace('\n',' ')[:54])
    archive=s.get('diagnostic_archive')
    if archive:parts.append('诊断包 '+Path(archive).name)
    else:parts.append(str(s.get('log_status') or ''))
    return ' · '.join(part for part in parts if part)

def route_indicator(s,route,closed=False):
    item=(s.get('routes') or {}).get(route) or {}
    name='USB主路' if route=='usb' else '蓝牙位置路'
    if closed:status='已断开'
    elif item.get('connecting'):status='连接中'
    elif item.get('quarantined'):status='收发异常，位置改走USB'
    elif item.get('connected') and item.get('ready'):status='已连接，有回包'
    elif item.get('connected'):status='已连接，等待回包'
    else:status='未连接'
    if item.get('error'):status+=' · '+str(item['error']).replace('\n',' ')[:80]
    return name+'：'+status

def position_route_text(s):
    route=s.get('position_route')
    if route=='ble':return '六位置按钮 / 已设快捷键 → 蓝牙位置路'
    if route=='usb':return '六位置按钮 / 已设快捷键 → USB主路（蓝牙未接通时仍可用）'
    return '六位置按钮 / 已设快捷键：等待可用链路'

def connection_banner(s):
    text=position_route_text(s);firmware=s.get('observed_firmware')
    return text+(f' · 实收FW {firmware:08X}' if isinstance(firmware,int) else '')

def keyboard_command(keysym,*,editable=False,active=True,held=(),keys=DEFAULT_POSITION_KEYS,char=None):
    """Return (opcode,payload,token) without accessing Tk or a device."""
    if not active:return None
    key=str(keysym).lower()
    if key=='space':result=(STOP,b'','stop')
    else:
        if editable:return None
        if key.startswith('kp_'):key=key[3:]
        if isinstance(char,str) and len(char)==1 and char.lower() in keys:key=char.lower()
        if key not in keys:return None
        index=keys.index(key)+1
        result=(POSITION,bytes((index,)),'position'+str(index))
    return None if result[2] in held else result

def keyboard_token(keysym,*,keys=DEFAULT_POSITION_KEYS,char=None):
    result=keyboard_command(keysym,keys=keys,char=char)
    return result[2] if result else None

class App:
    def __init__(self,root,settings_path=None):
        self.root=root;self.owner=None;self.closing=False;self.error=''
        self.primary_transport=None;self.disconnecting=False;self.held_keys=set();self.pressed_keycodes={}
        self.settings_path=Path(settings_path) if settings_path is not None else ROOT/'settings.json'
        settings,self.error=load_settings(self.settings_path);self.position_keys=settings['position_keys'];self.local_notice=''
        self.selected_keyboard=SelectedKeyboard();self.keyboard_window=None;self.keyboard_devices=[]
        self.keyboard_device=tk.StringVar(value=settings.get('keyboard_device',''))
        self.keyboard_status=tk.StringVar(value=self.selected_keyboard.status)
        root.title('V4 串口控制＋蓝牙位置');root.geometry('1130x800');root.minsize(1000,720)
        for name in ('TkDefaultFont','TkTextFont','TkMenuFont'):
            tkfont.nametofont(name,root=root).configure(family='Microsoft YaHei UI',size=-13)
        style=ttk.Style(root);style.configure('TLabel',font=('Microsoft YaHei UI',-13));style.configure('TButton',font=('Microsoft YaHei UI',-13),padding=(6,2))
        style.configure('TLabelframe.Label',font=('Microsoft YaHei UI',-13))
        outer=ttk.Frame(root,padding=10);outer.pack(fill='both',expand=True)
        row=ttk.Frame(outer);row.pack(fill='x')
        ttk.Label(row,text='USB主路 · COM（空=自动）').pack(side='left')
        self.usb_port=tk.StringVar(value=settings['usb_port'])
        self.usb_port_box=ttk.Combobox(row,textvariable=self.usb_port,width=9,values=(),state='normal');self.usb_port_box.pack(side='left',padx=6)
        ttk.Label(row,text='波特率').pack(side='left')
        self.usb_baud=tk.StringVar(value=str(settings['usb_baud']))
        ttk.Entry(row,textvariable=self.usb_baud,width=8).pack(side='left',padx=5)
        ttk.Button(row,text='刷新串口',command=self.refresh_usb_ports).pack(side='left',padx=3)
        self.usb_connect_button=ttk.Button(row,text='连接USB',command=self.connect_usb);self.usb_connect_button.pack(side='left',padx=4)
        self.connect_button=self.usb_connect_button
        self.disconnect_button=ttk.Button(row,text='停止并断开全部',command=self.disconnect);self.disconnect_button.pack(side='left',padx=4)
        row=ttk.Frame(outer);row.pack(fill='x',pady=(5,0))
        ttk.Label(row,text='蓝牙位置路 · BT10地址').pack(side='left')
        self.address=tk.StringVar(value=settings['ble_address'])
        ttk.Entry(row,textvariable=self.address,width=22).pack(side='left',padx=8)
        self.ble_connect_button=ttk.Button(row,text='连接蓝牙',command=self.connect_ble);self.ble_connect_button.pack(side='left',padx=4)
        self.ble_disconnect_button=ttk.Button(row,text='断开蓝牙',command=self.disconnect_ble);self.ble_disconnect_button.pack(side='left',padx=4)
        ttk.Label(row,text='位置键1–6').pack(side='left',padx=(8,0))
        self.keys_text=tk.StringVar(value=','.join(self.position_keys))
        ttk.Entry(row,textvariable=self.keys_text,width=15).pack(side='left',padx=5)
        ttk.Button(row,text='保存设置',command=self.save_local_settings).pack(side='left',padx=4)
        row=ttk.Frame(outer);row.pack(fill='x',pady=(5,0))
        self.usb_status=tk.StringVar(value='USB主路：未连接');self.ble_status=tk.StringVar(value='蓝牙位置路：未连接')
        self.usb_status_label=ttk.Label(row,textvariable=self.usb_status,wraplength=520)
        self.ble_status_label=ttk.Label(row,textvariable=self.ble_status,wraplength=520)
        self.usb_status_label.grid(row=0,column=0,sticky='ew',padx=(0,8));self.ble_status_label.grid(row=0,column=1,sticky='ew')
        row.columnconfigure(0,weight=1);row.columnconfigure(1,weight=1)
        self.identity=tk.StringVar(value='USB负责控制、参数与采集；接通蓝牙后六位置按钮和数字键经蓝牙。')
        ttk.Label(outer,textvariable=self.identity,wraplength=1060).pack(fill='x',pady=(5,3))
        row=ttk.Frame(outer);row.pack(fill='x',pady=4)
        tk.Button(row,text='STOP 停止',bg='#bb242d',fg='white',font=('Microsoft YaHei UI',-16,'bold'),
            padx=18,pady=4,command=lambda:self.send(STOP)).pack(side='left',padx=(0,12))
        for label,op in (('扶直校零',ZERO),('开始平衡',RUN),('清除故障',CLEAR)):
            ttk.Button(row,text=label,command=lambda opcode=op:self.send(opcode)).pack(side='left',padx=4)
        self.keyboard_button=ttk.Button(row,text='指定键盘设置',command=self.open_keyboard_settings);self.keyboard_button.pack(side='left',padx=8)
        self.notice=tk.StringVar(value='先停止并扶直校零，再开始平衡；六位置按钮按下时，以当时实测位置为原点。')
        ttk.Label(outer,textvariable=self.notice,foreground='#9b351b',wraplength=1060).pack(fill='x',pady=4)
        frame=ttk.LabelFrame(outer,text='六位置 · 空格固定STOP · 保存设置应用快捷键（输入框内位置键只输入）',padding=5);frame.pack(fill='x',pady=4)
        self.position_frame=frame
        self.position_buttons=[]
        for n in range(1,7):
            button=ttk.Button(frame,text=f'{self.position_keys[n-1]} · 相对 {n*60}°',command=lambda i=n:self.send(POSITION,payload=bytes((i,))))
            button.grid(row=0,column=n-1,sticky='ew',padx=4);frame.columnconfigure(n-1,weight=1);self.position_buttons.append(button)
        self.cancel_button=ttk.Button(frame,text='取消移动，平滑停转并保持',command=lambda:self.send(CANCEL_HOLD))
        self.cancel_button.grid(row=1,column=0,columnspan=6,sticky='w',padx=4,pady=(6,0))
        self.status=tk.StringVar(value='尚无板端数据');self.position=tk.StringVar(value='尚无位置数据');self.errors=tk.StringVar(value='尚无误差数据')
        for variable in (self.status,self.position,self.errors):
            ttk.Label(outer,textvariable=variable,wraplength=1060).pack(fill='x',pady=1)
        plot=ttk.LabelFrame(outer,text='误差 · 跟踪蓝色 / 最终位置橙色（计数） · 摆杆角绿色（°） · 零参考始终水平',padding=4)
        self.canvas=tk.Canvas(plot,height=200,bg='#fafbfc',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        frame=ttk.LabelFrame(outer,text='平衡参数 · 停止后才能提交，积分固定为0',padding=4)
        parameter_frame=frame
        self.drafts=[tk.StringVar(value=format(v,'.9g')) for v in DEFAULT_GAINS]
        for i,name in enumerate(NAMES):
            ttk.Label(frame,text=name).grid(row=0,column=i,padx=8,sticky='w')
            ttk.Entry(frame,textvariable=self.drafts[i],width=12,state='readonly' if i in (1,4) else 'normal').grid(row=1,column=i,padx=8,pady=2,sticky='ew')
            frame.columnconfigure(i,weight=1)
        self.submit=ttk.Button(frame,text='停机后提交',command=self.set_gains);self.submit.grid(row=2,column=0,columnspan=2,sticky='w',pady=2)
        ttk.Button(frame,text='独立读回',command=lambda:self.send(READ_GAINS,255)).grid(row=2,column=2,columnspan=2,sticky='w')
        self.gains=tk.StringVar(value='实际采用 / 已保存 / 独立读回：等待板端数据')
        ttk.Label(frame,textvariable=self.gains,wraplength=1030).grid(row=3,column=0,columnspan=6,sticky='w',pady=2)
        self.log=tk.StringVar(value='');ttk.Label(outer,textvariable=self.log,wraplength=1060).pack(side='bottom',fill='x',pady=2)
        parameter_frame.pack(side='bottom',fill='x',pady=3)
        plot.pack(fill='both',expand=True,pady=5)
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.refresh)
        root.bind('<Configure>',self._resize,add='+')
        root.bind('<KeyPress>',self._key_press,add='+');root.bind('<KeyRelease>',self._key_release,add='+')
        root.bind('<FocusOut>',self._focus_out,add='+')
        root.after(25,self._poll_selected_keyboard)
    def _resize(self,event):
        if event.widget is not self.root:return
        def walk(parent):
            for widget in parent.winfo_children():
                if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0)>0:
                    if widget in (self.usb_status_label,self.ble_status_label):widget.configure(wraplength=max(260,(self.root.winfo_width()-80)//2))
                    else:widget.configure(wraplength=max(300,self.root.winfo_width()-70))
                walk(widget)
        walk(self.root)
    def _apply_keys(self,keys):
        reader=getattr(self,'selected_keyboard',None)
        if reader and reader.active and list(keys)!=self.position_keys:
            reader.disable(status='位置键已改变，指定键盘已禁用；请重新启用')
            self.keyboard_status.set(reader.status);self.keyboard_button.configure(text='指定键盘设置')
        self.position_keys=list(keys);self.held_keys.clear();self.pressed_keycodes.clear()
        for index,button in enumerate(getattr(self,'position_buttons',())):
            button.configure(text=f'{keys[index]} · 相对 {(index+1)*60}°')
    def _settings_from_ui(self):
        return validate_settings(dict(usb_port=self.usb_port.get(),usb_baud=self.usb_baud.get(),
            ble_address=self.address.get(),position_keys=self.keys_text.get(),keyboard_device=self.keyboard_device.get()))
    def save_local_settings(self):
        try:
            settings=save_settings(self.settings_path,self._settings_from_ui());self._apply_keys(settings['position_keys'])
            self.usb_port.set(settings['usb_port']);self.usb_baud.set(str(settings['usb_baud']));self.address.set(settings['ble_address'])
            self.keys_text.set(','.join(settings['position_keys']));self.error=''
            self.local_notice='本机通信设置已保存并应用；未更改设备参数'
            self.notice.set(self.local_notice)
        except Exception as exc:self.error='保存设置失败：'+str(exc);self.notice.set(self.error)
    def refresh_usb_ports(self):
        try:
            ports=[item['port'] for item in list_usb_choices()]
            self.usb_port_box.configure(values=tuple(ports));self.error=''
            self.local_notice='USB端口：'+(', '.join(ports) if ports else '未发现')+'；未打开任何端口'
            self.notice.set(self.local_notice)
        except Exception as exc:self.error='刷新串口失败：'+str(exc);self.notice.set(self.error)
    def open_keyboard_settings(self):
        if self.keyboard_window is not None and self.keyboard_window.winfo_exists():
            self.keyboard_window.lift();return self.keyboard_window
        window=tk.Toplevel(self.root);self.keyboard_window=window;window.title('指定键盘设置 · 复用原RawInput模块')
        window.geometry(f'860x460+{self.root.winfo_x()+30}+{self.root.winfo_y()+40}');window.minsize(760,420);window.transient(self.root)
        body=ttk.Frame(window,padding=12);body.pack(fill='both',expand=True);body.columnconfigure(0,weight=1);body.rowconfigure(1,weight=1)
        ttk.Label(body,text='直接复用原 raw_keyboard.py；默认不注册。先枚举并选择唯一设备，再明确启用。',wraplength=800).grid(row=0,column=0,sticky='ew',pady=(0,6))
        listing=ttk.Frame(body);listing.grid(row=1,column=0,sticky='nsew',pady=4)
        scroll=ttk.Scrollbar(listing,orient='vertical');scroll.pack(side='right',fill='y')
        self.keyboard_list=tk.Listbox(listing,height=5,yscrollcommand=scroll.set,exportselection=False)
        self.keyboard_list.pack(side='left',fill='both',expand=True);scroll.configure(command=self.keyboard_list.yview)
        self.keyboard_list.bind('<<ListboxSelect>>',self._choose_keyboard_device)
        row=ttk.Frame(body);row.grid(row=2,column=0,sticky='ew',pady=6);row.columnconfigure(1,weight=1)
        ttk.Label(row,text='唯一路径片段').grid(row=0,column=0,sticky='w',padx=(0,8))
        ttk.Entry(row,textvariable=self.keyboard_device).grid(row=0,column=1,sticky='ew')
        row=ttk.Frame(body);row.grid(row=3,column=0,sticky='ew',pady=4)
        for text,command in (('枚举键盘',self.list_keyboards),('启用',self.enable_selected_keyboard),('禁用',self.disable_selected_keyboard),
                ('旧六位置 G–L',self.old_position_preset),('保存设置',self.save_local_settings)):
            ttk.Button(row,text=text,command=command).pack(side='left',padx=(0,8))
        status=ttk.Label(body,textvariable=self.keyboard_status,wraplength=800);status.grid(row=4,column=0,sticky='ew',pady=6)
        info=ttk.Label(body,text='启用后仅选定键盘的当前六位置键和空格STOP生效（后台也响应）；普通窗口位置键暂停，其他键盘文本不记录。\n设备拔除会解除并STOP。关闭此设置窗不会禁用。G–L预设只含六位置，不含旧起摆或轨迹。',wraplength=800)
        info.grid(row=5,column=0,sticky='ew',pady=4)
        def resize(event):
            if event.widget is window:
                for widget in (body.winfo_children()[0],status,info):widget.configure(wraplength=max(400,window.winfo_width()-40))
        window.bind('<Configure>',resize,add='+')
        return window
    def list_keyboards(self):
        try:
            from raw_keyboard import devices
            self.keyboard_devices=devices();self.keyboard_list.delete(0,tk.END)
            for device in self.keyboard_devices:self.keyboard_list.insert(tk.END,device['path'])
            self.keyboard_status.set(f'已枚举{len(self.keyboard_devices)}个键盘；尚未注册捕获，请选择唯一设备后启用')
        except Exception as exc:self.keyboard_status.set('枚举失败：'+str(exc))
    def _choose_keyboard_device(self,event=None):
        selected=self.keyboard_list.curselection()
        if selected:self.keyboard_device.set(self.keyboard_devices[selected[0]]['path'])
    def enable_selected_keyboard(self):
        try:
            keys=normalize_position_keys(self.keys_text.get());self._apply_keys(keys)
            self.selected_keyboard.enable(self.keyboard_device.get(),keys)
            self.keyboard_status.set(self.selected_keyboard.status);self.keyboard_button.configure(text='指定键盘已启用');self.error=''
        except Exception as exc:self.keyboard_status.set('启用失败：'+str(exc));self.error=str(exc)
    def disable_selected_keyboard(self):
        reader=getattr(self,'selected_keyboard',None)
        if reader:
            reader.disable()
            if hasattr(self,'keyboard_status'):self.keyboard_status.set(reader.status)
            if hasattr(self,'keyboard_button'):self.keyboard_button.configure(text='指定键盘设置')
    def old_position_preset(self):
        self.keys_text.set('g,h,i,j,k,l');self._apply_keys(list('ghijkl'))
        self.keyboard_status.set('已应用旧六位置G–L；保存设置后可跨重启保留，仅包含当前六位置')
    def _poll_selected_keyboard(self):
        if self.closing:return
        reader=self.selected_keyboard
        for opcode,payload,source in reader.poll():
            record_error=''
            try:
                diagnostics=getattr(self.owner,'diagnostics',None)
                if diagnostics:diagnostics.event('selected_keyboard_action',source=source,opcode=opcode,payload_hex=payload.hex())
            except Exception as exc:record_error='指定键盘记录失败：'+str(exc)
            self.send(opcode,payload=payload)
            if record_error:self.error=record_error
            if source=='RAW_KEYBOARD_REMOVED':
                try:reader.disable(status=reader.status)
                except Exception as exc:self.error='指定键盘关闭失败：'+str(exc)
        self.keyboard_status.set(reader.status);self.keyboard_button.configure(text='指定键盘已启用' if reader.active else '指定键盘设置')
        self.root.after(25,self._poll_selected_keyboard)
    def connect(self):
        self.connect_usb()
    def connect_usb(self):
        try:
            if self.owner and not self.owner.closed.is_set():raise RuntimeError('主连接仍在运行，请先停止并断开全部，再连接USB主路')
            port=normalize_usb_port(self.usb_port.get());baud=normalize_usb_baud(self.usb_baud.get())
            keys=normalize_position_keys(self.keys_text.get())
            selection=choose_transport(port)
            if selection['transport']!='usb':raise RuntimeError('未检测到USB串口；可点击“连接蓝牙”单独连接')
            self._apply_keys(keys);self.primary_transport='usb';self.disconnecting=False
            self.owner=DualOwner(self.address.get().strip().upper(),ROOT/'logs',transport='usb',port=selection['port'],baudrate=baud).start()
            self.error='';self.local_notice='';self.usb_connect_button.configure(state='disabled')
        except Exception as exc:
            self.error=str(exc)
            if getattr(exc,'choices',None):self.error+='：'+', '.join(item['port'] for item in exc.choices)
    def connect_ble(self):
        try:
            address=self.address.get().strip().upper()
            if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',address):raise RuntimeError('请输入完整的BT10地址')
            keys=normalize_position_keys(self.keys_text.get())
            if self.owner and not self.owner.closed.is_set():
                if self.disconnecting:raise RuntimeError('正在断开，请等待关闭完成')
                if self.primary_transport=='ble':raise RuntimeError('蓝牙主连接仍在运行，请先断开')
                self.owner.attach_ble(address)
            else:
                self.primary_transport='ble';self.disconnecting=False
                self.owner=DualOwner(address,ROOT/'logs',transport='ble').start()
            self._apply_keys(keys);self.error='';self.local_notice='';self.ble_connect_button.configure(state='disabled')
        except Exception as exc:self.error=str(exc)
    def disconnect_ble(self):
        if not self.owner:return
        try:
            if self.primary_transport=='ble':self.disconnect()
            else:self.owner.detach_ble()
        except Exception as exc:self.error=str(exc)
    def send(self,opcode,group=0,payload=b''):
        try:
            if not self.owner:raise RuntimeError('请先连接设备')
            self.owner.command(opcode,group,payload);self.error='';self.local_notice=''
        except Exception as exc:self.error=str(exc)
    def set_gains(self):
        try:
            if not self.owner:raise RuntimeError('请先连接设备')
            self.owner.set_gains([v.get() for v in self.drafts]);self.error=''
        except Exception as exc:self.error=str(exc)
    def _keyboard_focus(self):
        try:
            focus=self.root.focus_get()
            return focus if focus and focus.winfo_toplevel() is self.root else None
        except (AttributeError,tk.TclError):return None
    def _key_press(self,event):
        if self.closing:return
        focus=self._keyboard_focus()
        editable=bool(focus and focus.winfo_class() in ('Entry','TEntry','Text','TCombobox','Spinbox','TSpinbox'))
        result=keyboard_command(event.keysym,editable=editable,active=focus is not None,held=self.held_keys,
            keys=self.position_keys,char=getattr(event,'char',None))
        if not result:return
        if result[0]==POSITION and getattr(getattr(self,'selected_keyboard',None),'active',False):return
        opcode,payload,token=result;self.held_keys.add(token)
        self.pressed_keycodes[getattr(event,'keycode',str(event.keysym).lower())]=token
        record_error=''
        try:
            diagnostics=getattr(self.owner,'diagnostics',None)
            if diagnostics:diagnostics.event('ui_keyboard',key=str(event.keysym),opcode=opcode,payload_hex=payload.hex())
        except Exception as exc:record_error='键盘操作记录失败：'+str(exc)
        self.send(opcode,payload=payload)
        if record_error:self.error=record_error
        return 'break'
    def _key_release(self,event):
        token=self.pressed_keycodes.pop(getattr(event,'keycode',str(event.keysym).lower()),None)
        if token is None:token=keyboard_token(event.keysym,keys=self.position_keys,char=getattr(event,'char',None))
        if token:self.held_keys.discard(token)
    def _focus_out(self,event):
        if not self.closing:self.root.after_idle(self._clear_inactive_keys)
    def _clear_inactive_keys(self):
        if self._keyboard_focus() is None:self.held_keys.clear();self.pressed_keycodes.clear()
    def disconnect(self):
        self.held_keys.clear();self.pressed_keycodes.clear();self.disconnecting=True
        if self.owner:self.owner.stop(wait=False)
        self.disable_selected_keyboard()
    def close(self):
        if self.closing:return
        self.closing=True;self.disconnect();self._close_when_ready()
    def _close_when_ready(self):
        if not self.owner or self.owner.closed.is_set():self.root.destroy();return
        self.root.after(100,self._close_when_ready)
    def refresh(self):
        if self.closing:return
        if self.owner:
            s=self.owner.state.snapshot()
            closed=self.owner.closed.is_set()
            self.usb_status.set(route_indicator(s,'usb',closed));self.ble_status.set(route_indicator(s,'ble',closed))
            self.identity.set(connection_banner(s))
            self.notice.set(self.error or s['error'] or s['log_error'] or self.local_notice or s.get('ble_notice') or s['message'])
            self.status.set(telemetry_text(s)[0]);self.position.set(telemetry_text(s)[1]);self.errors.set(telemetry_text(s)[2])
            def fmt(values):return ' / '.join(format(v,'.7g') for v in values) if values else '等待数据'
            d=s['diagnostic']
            actual=fmt(d['active_gains']) if d else '等待数据'
            age=f"（{max(0,time.monotonic()-s['diagnostic_rx']):.1f}s前诊断）" if d else ''
            self.gains.set(f"实际采用{age}：{actual}\n已保存：{fmt(s['stored'])}    独立读回：{fmt(s['independent'])}")
            ready=bool(not closed and not self.disconnecting and s['control_fresh'] and s['latest']['mode']==3 and not s['latest']['fault'] and not s['error'] and not s['log_error'])
            for button in self.position_buttons+[self.cancel_button]:button.configure(state='normal' if ready else 'disabled')
            writable,_=self.owner.parameter_write_status();self.submit.configure(state='normal' if writable and not closed and not self.disconnecting else 'disabled')
            ble=(s.get('routes') or {}).get('ble') or {}
            self.usb_connect_button.configure(state='normal' if closed else 'disabled')
            can_attach=closed or (self.primary_transport=='usb' and s.get('connected') and not self.disconnecting and not(ble.get('connected') or ble.get('connecting')))
            self.ble_connect_button.configure(state='normal' if can_attach else 'disabled')
            self.ble_disconnect_button.configure(state='normal' if not closed and (self.primary_transport=='ble' or ble.get('connected') or ble.get('connecting')) else 'disabled')
            self.disconnect_button.configure(state='disabled' if closed else 'normal')
            self.log.set(link_summary(s))
            self.draw(s['history'])
        else:
            for button in self.position_buttons+[self.cancel_button,self.submit]:button.configure(state='disabled')
            self.disconnect_button.configure(state='disabled');self.ble_disconnect_button.configure(state='disabled')
            if self.error:self.notice.set(self.error)
        self.root.after(100,self.refresh)
    def draw(self,history):
        c=self.canvas;c.delete('all');w=max(300,c.winfo_width());h=max(30,c.winfo_height())
        if len(history)<2:c.create_text(w/2,h/2,text='等待平衡数据',fill='#77818f');return
        if h<110:c.create_text(w/2,h/2,text='放大窗口查看完整误差波形',fill='#77818f');return
        end=history[-1]['board_ms'];rows=[r for r in history if ((end-r['board_ms'])&0xffffffff)<=12000]
        if len(rows)<2:return
        left=70;right=w-12;span=max(100,end-rows[0]['board_ms'])
        def panel(top,bottom,series,unit,minimum):
            limit=max(minimum,max(abs(fn(r)) for _,_,fn in series for r in rows))*1.12
            middle=(top+bottom)/2
            c.create_line(left,middle,right,middle,fill='#6b7785',width=1)
            for value,y in ((limit,top),(0,middle),(-limit,bottom)):
                c.create_text(left-8,y,text=f'{value:.1f}',anchor='e',fill='#657183')
            c.create_text(left+4,top+2,text=unit,anchor='nw',fill='#657183')
            for color,width,fn in series:
                points=[]
                for r in rows:
                    points.extend((left+(r['board_ms']-rows[0]['board_ms'])/span*(right-left),middle-fn(r)/limit*(bottom-top)/2))
                c.create_line(*points,fill=color,width=width)
        panel(12,h*.51,(
            ('#cf7c20',1,lambda r:r['wide_encoder']-r['final_target']),
            ('#3283c6',2,lambda r:r['wide_encoder']-r['wide_target'])), '位置误差 / 计数',2)
        panel(h*.62,h-26,(('#15805b',2,lambda r:r['angle_error']*345/4096),), '摆杆角误差 / °',.5)
        c.create_text(left,h-10,text=f'-{span/1000:.1f}s',anchor='w',fill='#657183');c.create_text(right,h-10,text='现在',anchor='e',fill='#657183')

def main():
    enable_dpi_awareness();root=tk.Tk();App(root);root.mainloop()

if __name__=='__main__':main()
