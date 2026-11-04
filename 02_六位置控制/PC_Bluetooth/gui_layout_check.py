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
        ui.notice.set('先停止并扶直校零，再开始平衡；六位置按钮按下时，以当时实测位置为原点。')
        ui.position.set('实测 123456 / 42733.846°    当前参考 123455 / 42733.500°    最终目标 123625 / 42792.115°')
        ui.errors.set('跟踪误差 +1计数 / +0.346°    最终误差 -169计数 / -58.500°    摆杆角误差 +0.168°（目标 +0.000°）')
        ui.gains.set('实际采用（0.1s前诊断）：49 / 0 / 210 / 20 / 0 / 700\n已保存：49 / 0 / 210 / 20 / 0 / 700    独立读回：49 / 0 / 210 / 20 / 0 / 700')
        sizes=[]
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        for w,h in ((1130,800),(1000,720)):
            root.geometry(f'{w}x{h}+30000+30000');root.update();root.update_idletasks()
            assert ui.owner is None
            outside=[]
            x0,y0=root.winfo_rootx(),root.winfo_rooty()
            for widget in walk(root):
                if not widget.winfo_ismapped():continue
                x,y=widget.winfo_rootx()-x0,widget.winfo_rooty()-y0
                if x<0 or y<0 or x+widget.winfo_width()>root.winfo_width()+1 or y+widget.winfo_height()>root.winfo_height()+1:
                    outside.append(dict(widget=str(widget),class_name=widget.winfo_class(),x=x,y=y,w=widget.winfo_width(),h=widget.winfo_height()))
            assert not outside,(w,h,outside)
            overlap=[]
            for parent in [root,*walk(root)]:
                children=[c for c in parent.winfo_children() if c.winfo_ismapped()]
                for i,a in enumerate(children):
                    for b in children[i+1:]:
                        if (max(a.winfo_x(),b.winfo_x())<min(a.winfo_x()+a.winfo_width(),b.winfo_x()+b.winfo_width()) and
                            max(a.winfo_y(),b.winfo_y())<min(a.winfo_y()+a.winfo_height(),b.winfo_y()+b.winfo_height())):
                            overlap.append((str(a),str(b)))
            assert not overlap,(w,h,overlap)
            sizes.append(dict(width=root.winfo_width(),height=root.winfo_height(),widgets_checked=len(list(walk(root))),outside=outside,sibling_overlap=overlap))
        assert tuple(threading.enumerate())==before
        report=dict(status='PASS',scope='Actual Tk construction and containment at default/minimum size; window placed offscreen and destroyed. No BLE owner created.',sizes=sizes,
            gui_windows_created=1,gui_windows_destroyed=1,threads_started=0,device_connections=0,owner_created=False)
        (Path(__file__).parent/'GUI_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:root.destroy()

if __name__=='__main__':main()
