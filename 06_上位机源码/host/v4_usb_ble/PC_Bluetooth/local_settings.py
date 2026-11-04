"""Local PC communication settings only; no hardware or controller parameters."""
from pathlib import Path
import json
import os
import re
import tempfile

DEFAULT_POSITION_KEYS=tuple('123456')
DEFAULT_SETTINGS=dict(usb_port='',usb_baud=230400,ble_address='D0:54:7B:AA:0C:84',position_keys=list(DEFAULT_POSITION_KEYS),keyboard_device='')

def defaults():return dict(DEFAULT_SETTINGS,position_keys=list(DEFAULT_POSITION_KEYS))

def normalize_usb_port(value):
    if not isinstance(value,str):raise ValueError('USB端口必须是COM号或留空自动')
    value=value.strip().upper()
    if value and not re.fullmatch(r'COM[1-9][0-9]*',value):raise ValueError('USB端口请填写COM号，例如COM5；留空表示自动')
    return value

def normalize_usb_baud(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)):raise ValueError('USB波特率必须是整数')
    text=str(value).strip()
    if not text.isascii() or not text.isdecimal():raise ValueError('USB波特率必须是整数')
    baud=int(text)
    if not 300<=baud<=4000000:raise ValueError('USB波特率须在300至4000000之间；板端与电脑必须一致')
    return baud

def normalize_ble_address(value):
    if not isinstance(value,str):raise ValueError('蓝牙地址必须是文本')
    value=value.strip().upper()
    if value and not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',value):raise ValueError('蓝牙地址请填写完整MAC，例如D0:54:7B:AA:0C:84')
    return value

def normalize_position_keys(value):
    if isinstance(value,str):value=value.replace('，',',').split(',')
    if not isinstance(value,(list,tuple)) or len(value)!=6:raise ValueError('需要6个位置按键，用英文逗号分隔，例如q,w,e,a,s,d')
    result=[]
    for index,key in enumerate(value,1):
        if not isinstance(key,str):raise ValueError(f'位置{index}的按键必须是单个字符')
        if key.lower() in ('space','空格') or (key and key.isspace()):raise ValueError(f'位置{index}不能使用空格；空格固定为STOP')
        key=key.strip().lower()
        if not key:raise ValueError(f'位置{index}的按键为空')
        if len(key)!=1 or not key.isprintable() or key.isspace():raise ValueError(f'位置{index}的按键必须是单个可打印字符')
        if key in result:raise ValueError(f'按键{key}重复：位置{result.index(key)+1}和位置{index}')
        result.append(key)
    return result

def validate_settings(value):
    if not isinstance(value,dict):raise ValueError('settings.json必须是JSON对象')
    unknown=set(value)-set(DEFAULT_SETTINGS)
    if unknown:raise ValueError('不支持的本地设置项：'+','.join(sorted(unknown)))
    merged=defaults();merged.update(value)
    if not isinstance(merged['keyboard_device'],str):raise ValueError('指定键盘路径片段必须是文本')
    return dict(usb_port=normalize_usb_port(merged['usb_port']),usb_baud=normalize_usb_baud(merged['usb_baud']),
        ble_address=normalize_ble_address(merged['ble_address']),position_keys=normalize_position_keys(merged['position_keys']),
        keyboard_device=merged['keyboard_device'].strip())

def load_settings(path):
    path=Path(path)
    if not path.exists():return defaults(),''
    try:return validate_settings(json.loads(path.read_text(encoding='utf-8-sig'))),''
    except (OSError,ValueError,TypeError) as exc:return defaults(),'本地通信设置读取失败，已恢复默认：'+str(exc)

def save_settings(path,value):
    value=validate_settings(value);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',dir=path.parent,
                prefix='.'+path.name+'.',suffix='.tmp',delete=False) as output:
            temporary=Path(output.name);json.dump(value,output,ensure_ascii=False,indent=2)
            output.write('\n');output.flush();os.fsync(output.fileno())
        os.replace(temporary,path);temporary=None
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return value
