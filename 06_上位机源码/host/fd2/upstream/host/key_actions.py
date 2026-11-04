"""Selected keypad down/up edges; autorepeat never creates a new action."""
import json
from pathlib import Path
from protocol import STOP,START_SWING,SELECT_SWING,CANCEL_HOLD,POSITION,TRAJECTORY
DEFAULT={
 '1':dict(zip('ABCDEF',['STOP','START','SWM4','SA21','144','HOLD'])),
 '2':dict(zip('GHIJKL',[f'POSITION_{n}' for n in range(1,7)])),
 '3':dict(zip('MNOPQR',[f'TRAJECTORY_{n}' for n in range(1,7)]))}
def load_mapping(path):
    data=json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else DEFAULT
    if set(data)!={'1','2','3'}:raise ValueError('需要三层键盘配置')
    actions={'STOP','START','SWM4','SA21','144','HOLD',*[f'POSITION_{n}' for n in range(1,7)],*[f'TRAJECTORY_{n}' for n in range(1,7)]}
    data={layer:{key.upper():action for key,action in entries.items()} for layer,entries in data.items()}
    for layer,entries in data.items():
        if len(entries)!=6 or any(len(key)!=1 or key not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' or action not in actions for key,action in entries.items()):raise ValueError('每层必须恰有六个字母或主键区数字键，不区分大小写')
    if set(data['1'].values())!={'STOP','START','SWM4','SA21','144','HOLD'}:raise ValueError('第一层必须保留六个动作入口')
    if set(data['2'].values())!={f'POSITION_{n}' for n in range(1,7)}:raise ValueError('第二层必须保留六个位置，不能占用一个作STOP')
    if set(data['3'].values())!={f'TRAJECTORY_{n}' for n in range(1,7)}:raise ValueError('第三层必须保留六条轨迹')
    return data
class KeyActions:
    def __init__(self,owner,mapping):self.owner=owner;self.mapping=mapping;self.layer='1';self.held=set();self.armed=False;self.wait_release=True
    def arm(self,held=()):self.held=set(held);self.wait_release=bool(self.held);self.armed=True
    def disarm(self):self.armed=False;self.wait_release=True
    def set_layer(self,layer):
        if str(layer) not in self.mapping:raise ValueError('未知键盘层')
        self.layer=str(layer);self.wait_release=bool(self.held)
    def on_key(self,vkey,down):
        relevant={ord(key.upper()) for entries in self.mapping.values() for key in entries}
        if vkey not in relevant:return
        if not down:
            self.held.discard(vkey)
            if not self.held:self.wait_release=False
            return
        if vkey in self.held:return
        self.held.add(vkey)
        if not self.armed or self.wait_release:return
        action=self.mapping[self.layer].get(chr(vkey).upper())
        if not action:return
        source=f'KEYBOARD/L{self.layer}/{action}'
        self.owner.trace('key_edge',layer=self.layer,action=action,edge='down')
        if getattr(getattr(self.owner,'state',None),'variant',None)==5 and action in ('START','SWM4','SA21','144'):
            self.owner.trace('key_rejected',layer=self.layer,action=action,reason='swing_removed',message='V5已移除起摆；请手动扶直、ZERO后使用窗口RUN')
            return
        try:
            if action=='STOP':self.owner.command(STOP,source=source)
            elif action=='START':self.owner.command(START_SWING,source=source)
            elif action=='HOLD':self.owner.command(CANCEL_HOLD,source=source)
            elif action in ('SWM4','SA21','144'):self.owner.command(SELECT_SWING,payload=bytes((('SWM4','SA21','144').index(action),)),source=source)
            elif action.startswith('POSITION_'):self.owner.command(POSITION,payload=bytes((int(action[-1]),)),source=source)
            else:self.owner.command(TRAJECTORY,payload=bytes((int(action[-1]),)),source=source)
        except Exception as exc:self.owner.trace('key_rejected',layer=self.layer,action=action,reason=str(exc))
    def removed(self):
        self.disarm();self.owner.trace('keyboard_removed')
        try:self.owner.command(STOP,source='KEYBOARD/REMOVED')
        except Exception as exc:self.owner.trace('keyboard_stop_unknown',reason=str(exc))
