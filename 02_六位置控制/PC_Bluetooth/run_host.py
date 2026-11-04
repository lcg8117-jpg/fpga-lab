"""Manual BLE desktop entry; importing/starting does not connect or move hardware."""
from pathlib import Path
import sys,traceback
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
def main():
    try:
        import app
        app.main()
    except BaseException:
        detail=traceback.format_exc()
        try:
            (ROOT/'logs').mkdir(exist_ok=True)
            (ROOT/'logs/startup_error.log').write_text(detail,encoding='utf-8')
        except OSError:pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动失败，完整错误在PC_Bluetooth/logs。\n\n'+detail[-2500:],'V4 蓝牙窗口启动失败',0x10)
        except Exception:pass
        raise SystemExit(1)
if __name__=='__main__':main()
