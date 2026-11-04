"""Two independently parsed physical routes, one logical request/session owner."""
import struct
from client import open_serial
class DuplexPort:
    records_physical_io=True
    async_write=True
    def __init__(self,usb_port,ble_address,*,logger=None,event_sink=None,serial_factory=open_serial,ble_factory=None):
        self.logger=logger;self.event_sink=event_sink;self.usb=None;self.ble=None;self.route_errors={};self.reported_errors={}
        try:
            if ble_address:
                if ble_factory is None:
                    from ble_transport import BLEPort
                    ble_factory=BLEPort
                self.ble=ble_factory(ble_address,io_sink=self.io,event_sink=event_sink)
            # Open USB only when BLE discovery/startup synchronization has
            # completed, so its finite driver buffer is not left unread.
            if usb_port:self.usb=serial_factory(usb_port,2000000)
            if not self.usb and not self.ble:raise ValueError('Select USB and/or an explicit BLE address')
        except Exception:
            self.close();raise
    def io(self,route,direction,raw):
        if self.logger and hasattr(self.logger,'io'):self.logger.io(route,direction,raw)
    def route_for_source(self,source):
        if self.ble is not None and (self.usb is None or source.startswith('KEYBOARD/')):return 'ble'
        if source=='heartbeat' or self.ble is None:return 'usb'
        return 'mirror'
    def write(self,raw,*,route='mirror'):
        rid=struct.unpack_from('<I',raw,8)[0];success=0
        selected=(0,1) if route=='mirror' else (1,) if route=='ble' else (0,)
        for route_index,port in enumerate((self.usb,self.ble)):
            if route_index not in selected or port is None or route_index in self.route_errors:continue
            try:
                count=port.write(raw)
                if route_index==0:
                    self.io(0,1,raw[:count] if count is not None else raw)
                    if self.event_sink:self.event_sink('physical_write',route=0,request_id=rid,bytes=count)
                if count is not None and count!=len(raw):raise OSError('partial USB request; outcome unknown; never retried')
                success+=1
            except Exception as exc:
                self.route_errors[route_index]=str(exc)
                if self.event_sink:self.event_sink('write_unknown',route=route_index,request_id=rid,reason=str(exc))
        if not success:raise OSError('No writable route remains; no reconnect or replay')
        return len(raw)
    def read_routes(self,n):
        result=[]
        for route,port in enumerate((self.usb,self.ble)):
            if port is None or route in self.route_errors:continue
            try:
                raw=port.read(n)
                if raw:
                    if route==0:self.io(0,0,raw)
                    result.append((route,raw))
            except Exception as exc:self.route_errors[route]=str(exc)
        if not result and len(self.route_errors)==sum(x is not None for x in (self.usb,self.ble)):raise OSError('All routes lost; watchdog remains active')
        return result
    def close(self):
        for port in (self.ble,self.usb):
            if port is not None:
                try:port.close() if hasattr(port,'close') else port.__exit__(None,None,None)
                except Exception:pass
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()
