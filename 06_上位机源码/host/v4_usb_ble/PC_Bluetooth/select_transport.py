"""Choose USB before BLE only after the user explicitly asks to connect."""
from serial_transport import serial_api

class TransportSelectionError(RuntimeError):
    def __init__(self,message,choices):
        super().__init__(message);self.choices=choices

def list_usb_choices(*,list_ports_factory=None):
    if list_ports_factory is None:
        serial_api()
        from serial.tools import list_ports
        list_ports_factory=list_ports.comports
    choices=[]
    for item in list_ports_factory():
        # Bluetooth virtual COM devices have no USB VID. Never probe those.
        if getattr(item,'vid',None) is None:continue
        name=str(getattr(item,'device','')).strip()
        if not name:continue
        choices.append(dict(port=name,description=str(getattr(item,'description','') or ''),
            vid=int(item.vid),pid=int(item.pid) if getattr(item,'pid',None) is not None else None,
            serial_number=getattr(item,'serial_number',None),manufacturer=getattr(item,'manufacturer',None)))
    return sorted(choices,key=lambda item:item['port'].casefold())

def choose_transport(port_hint='',*,list_ports_factory=None):
    choices=list_usb_choices(list_ports_factory=list_ports_factory)
    hint=str(port_hint or '').strip()
    if hint:
        match=next((item for item in choices if item['port'].casefold()==hint.casefold()),None)
        if match is None:
            raise TransportSelectionError('指定端口不是当前可用的USB串口；蓝牙虚拟COM不会使用',choices)
        return dict(transport='usb',port=match['port'],choices=choices)
    if not choices:return dict(transport='ble',port=None,choices=[])
    if len(choices)>1:
        raise TransportSelectionError('检测到多个USB串口，请明确选择板卡端口后连接',choices)
    return dict(transport='usb',port=choices[0]['port'],choices=choices)
