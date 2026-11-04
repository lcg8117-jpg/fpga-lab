"""BT10 profile from vendor manual 2026-09-18 and observed advertisement."""
SERVICE='0000ffe0-0000-1000-8000-00805f9b34fb'
NOTIFY='0000ffe1-0000-1000-8000-00805f9b34fb'
WRITE='0000ffe2-0000-1000-8000-00805f9b34fb'
UART_BAUD=230400
# The actual BT10 advertisement reports BluetoothAddressType.RANDOM (1).
# Choosing the default public-address requester can find stale cached services
# while every real GATT operation reports Unreachable.
WINRT_OPTIONS={'address_type':'random','use_cached_services':False}
FPGA_PINS={'module_TXD':'AA6','module_RXD':'Y6','module_STATE':'Y3'}
