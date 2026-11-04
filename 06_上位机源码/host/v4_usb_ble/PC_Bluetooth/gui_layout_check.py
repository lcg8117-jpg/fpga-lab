"""Create the real Tk interface offscreen; never create a BLE owner/device."""
from pathlib import Path
import importlib.abc
import json
import sys
import threading

class DeviceBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'serial','bleak','winrt','bluetooth'}:raise RuntimeError('GUI QA forbids devices')
sys.meta_path.insert(0,DeviceBlocker())
before=tuple(threading.enumerate())
import tkinter as tk
import app

def main():
    app.enable_dpi_awareness();root=tk.Tk();root.geometry('1130x800+30000+30000')
    try:
        ui=app.App(root);assert ui.owner is None
        ui.notice.set('通信异常：CRC实收FW56345237/schema56345207，与本程序协议不匹配；控制已锁定。')
        sample=dict(connected=True,authenticated=True,transport='usb',observed_firmware=0x5634523A,observed_schema=0x56345208,
            routes={'usb':dict(connected=True,ready=True),'ble':dict(connected=True,ready=True)},position_route='ble')
        ui.identity.set(app.connection_banner(sample))
        ui.usb_status.set(app.route_indicator(sample,'usb'));ui.ble_status.set(app.route_indicator(sample,'ble'))
        ui.log.set(app.link_summary(dict(rx_bytes=12345678,crc_errors=12,format_errors=123,timeouts=12,
            connection_metadata={'mtu_size':247,'connection_interval_ms':30,'rx_dropped_bytes':4096},
            diagnostic_last_rejection='ACK固件实收56345237，期望56345239',
            diagnostic_archive='D:/转发给队友/diagnostic_raw_20261103_120000.zip')))
        ui.position.set('实测 123456 / 42733.846°    当前参考 123455 / 42733.500°    最终目标 123625 / 42792.115°')
        ui.errors.set('跟踪误差 +1计数 / +0.346°    最终误差 -169计数 / -58.500°    摆杆角误差 +0.168°（目标 +0.000°）')
        ui.gains.set('实际采用（0.1s前诊断）：53.78 / 0 / 210.14 / 17.67 / 0 / 616.61\n已保存：53.78 / 0 / 210.14 / 17.67 / 0 / 616.61    独立读回：53.78 / 0 / 210.14 / 17.67 / 0 / 616.61')
        sizes=[]
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        for w,h in ((1130,800),(1000,720)):
            root.geometry(f'{w}x{h}+30000+30000');root.update();root.update_idletasks()
            assert ui.owner is None
            outside=[];hidden=[]
            x0,y0=root.winfo_rootx(),root.winfo_rooty()
            for widget in walk(root):
                if not widget.winfo_ismapped():hidden.append(str(widget));continue
                x,y=widget.winfo_rootx()-x0,widget.winfo_rooty()-y0
                if x<0 or y<0 or x+widget.winfo_width()>root.winfo_width()+1 or y+widget.winfo_height()>root.winfo_height()+1:
                    outside.append(dict(widget=str(widget),class_name=widget.winfo_class(),x=x,y=y,w=widget.winfo_width(),h=widget.winfo_height()))
            assert not outside,(w,h,outside)
            assert not hidden,(w,h,'hidden controls',hidden)
            assert ui.canvas.winfo_height()>=30,(w,h,'plot too small',ui.canvas.winfo_height())
            overlap=[]
            for parent in [root,*walk(root)]:
                children=[c for c in parent.winfo_children() if c.winfo_ismapped()]
                for i,a in enumerate(children):
                    for b in children[i+1:]:
                        if (max(a.winfo_x(),b.winfo_x())<min(a.winfo_x()+a.winfo_width(),b.winfo_x()+b.winfo_width()) and
                            max(a.winfo_y(),b.winfo_y())<min(a.winfo_y()+a.winfo_height(),b.winfo_y()+b.winfo_height())):
                            overlap.append((str(a),str(b)))
            assert not overlap,(w,h,overlap)
            sizes.append(dict(width=root.winfo_width(),height=root.winfo_height(),widgets_checked=len(list(walk(root))),outside=outside,hidden=hidden,plot_height=ui.canvas.winfo_height(),sibling_overlap=overlap))
        popup=ui.open_keyboard_settings();popup_sizes=[]
        ui.keyboard_status.set('指定键盘已禁用；普通窗口键盘可用。唯一路径片段保存在本机settings.json；仅明确启用才注册RawInput。')
        ui.keyboard_list.insert(tk.END,'HID#VID_TEST&PID_FAKE#GUI_LAYOUT_ONLY_NOT_A_REAL_DEVICE')
        for w,h in ((860,460),(760,420)):
            popup.geometry(f'{w}x{h}+30000+30000');root.update();root.update_idletasks()
            assert ui.owner is None and not ui.selected_keyboard.active and ui.selected_keyboard.reader is None
            outside=[];hidden=[];x0,y0=popup.winfo_rootx(),popup.winfo_rooty()
            for widget in walk(popup):
                if not widget.winfo_ismapped():hidden.append(str(widget));continue
                x,y=widget.winfo_rootx()-x0,widget.winfo_rooty()-y0
                if x<0 or y<0 or x+widget.winfo_width()>popup.winfo_width()+1 or y+widget.winfo_height()>popup.winfo_height()+1:
                    outside.append(dict(widget=str(widget),x=x,y=y,w=widget.winfo_width(),h=widget.winfo_height()))
            assert not outside and not hidden,(w,h,outside,hidden)
            popup_sizes.append(dict(width=popup.winfo_width(),height=popup.winfo_height(),widgets_checked=len(list(walk(popup))),outside=outside,hidden=hidden))
        popup.destroy()
        assert tuple(threading.enumerate())==before
        report=dict(status='PASS',scope='Actual Tk construction and containment at default/minimum size; window placed offscreen and destroyed. No BLE owner created.',sizes=sizes,
            keyboard_popup_sizes=popup_sizes,gui_windows_created=2,gui_windows_destroyed=2,threads_started=0,device_connections=0,owner_created=False,rawinput_registrations=0,keyboard_enumerations=0)
        (Path(__file__).parent/'GUI_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:root.destroy()

if __name__=='__main__':main()
