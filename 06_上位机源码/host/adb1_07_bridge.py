"""07 CommandFlow bridge; the original release owns wire and command semantics."""
import sys
from pathlib import Path

from host.adb1_fastp_bridge import ADB1FastPBridge, install as install_original
from host.adb1_07.host import protocol

S1_GAINS = (53.78, 0., 210.14, 17.67, 0., 616.61)


def load_upstream():
    vendor = str(Path(__file__).resolve().parent/'adb1_07/vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from host.adb1_07.host import client, app
    # UI draft only: no owner exists and no device command is issued here.
    protocol.DEFAULT_GAINS = S1_GAINS
    return dict(protocol=protocol, client=client, app=app)


class ADB107Bridge(ADB1FastPBridge):
    protocol = protocol
    epoch_stride = 1
    schema = '41444203'
    delivery_profile = 'ADB1_07'


def install(bridge, modules=None):
    install_original(bridge, modules or load_upstream())
