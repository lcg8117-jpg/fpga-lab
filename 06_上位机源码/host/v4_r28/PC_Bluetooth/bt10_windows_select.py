"""Live WinRT selection for an explicitly selected BT10. No protocol overrides."""
import asyncio

def install(target_address):
    import ble_transport
    Client,OriginalScanner=ble_transport.api()
    from bt10_profile import SERVICE,NOTIFY,WRITE
    from bleak.backends.device import BLEDevice
    from winrt.windows.devices.bluetooth import BluetoothLEDevice,BluetoothAddressType,BluetoothCacheMode
    class CurrentTargetScanner:
        @staticmethod
        async def find_device_by_address(address,timeout=8):
            if address.upper()!=target_address.upper():
                return await OriginalScanner.find_device_by_address(address,timeout=timeout)
            device=None;services=[]
            try:
                device=await asyncio.wait_for(BluetoothLEDevice.from_bluetooth_address_with_bluetooth_address_type_async(
                    int(address.replace(':',''),16),BluetoothAddressType.RANDOM),timeout=timeout)
                if device is None:raise RuntimeError('BT10 device unavailable')
                result=await asyncio.wait_for(device.get_gatt_services_async(BluetoothCacheMode.UNCACHED),timeout=timeout)
                services=list(result.services)
                service=next((s for s in services if str(s.uuid).lower()==SERVICE.lower()),None)
                if int(result.status)!=0 or service is None:
                    raise RuntimeError('BT10 current GATT service query failed: '+str(result.status))
                chars=await asyncio.wait_for(service.get_characteristics_async(BluetoothCacheMode.UNCACHED),timeout=timeout)
                found={str(c.uuid).lower():int(c.characteristic_properties) for c in chars.characteristics}
                if int(chars.status)!=0 or not(found.get(NOTIFY.lower(),0)&0x30) or not(found.get(WRITE.lower(),0)&0x0c):
                    raise RuntimeError('BT10 notify/write characteristics do not match')
                return BLEDevice(address,device.name or 'WHEELTEC',{'source':'live_uncached_GATT_verified'})
            finally:
                for service in services:service.close()
                if device:device.close()
    ble_transport.api=lambda:(Client,CurrentTargetScanner)
