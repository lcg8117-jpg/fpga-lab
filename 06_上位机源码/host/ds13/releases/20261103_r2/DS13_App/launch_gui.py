"""Explicit packaged GUI entry. Does not connect, write parameters or run the board."""
from pathlib import Path
import argparse,ctypes,json,os,sys,traceback
ROOT=Path(__file__).resolve().parent
os.environ['TCL_LIBRARY']=str(ROOT/'runtime/tcl/tcl8.6')
os.environ['TK_LIBRARY']=str(ROOT/'runtime/tcl/tk8.6')
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'host/vendor')]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-window',action='store_true')
    parser.add_argument('--window-size',default='')
    args=parser.parse_args()
    import app
    assert Path(app.__file__).resolve()==ROOT/'host/app.py'
    if args.verify_window:
        def forbidden(*a,**k):raise AssertionError('Verification must not connect to hardware')
        app.SerialOwner=forbidden
    window=app.App()
    width=min(1240,max(320,window.winfo_screenwidth()-40))
    height=min(720,max(300,window.winfo_screenheight()-90))
    if args.window_size:width,height=map(int,args.window_size.split('x'))
    window.minsize(min(1040,width),min(620,height))
    window.geometry(f'{width}x{height}')
    if args.verify_window:
        window.withdraw();window.update_idletasks()
        from ctypes import wintypes as wt
        user=ctypes.windll.user32
        user.GetParent.argtypes=[wt.HWND];user.GetParent.restype=wt.HWND
        hwnd=user.GetParent(window.winfo_id())
        user.GetWindowLongW.argtypes=[wt.HWND,ctypes.c_int];user.GetWindowLongW.restype=ctypes.c_long
        user.SetWindowLongW.argtypes=[wt.HWND,ctypes.c_int,ctypes.c_long]
        user.SetWindowLongW(hwnd,-20,user.GetWindowLongW(hwnd,-20)|0x08000000)
        window.geometry(f'{width}x{height}-20000-20000');window.deiconify()
    window.update_idletasks()
    assert window.editor.canvas.winfo_exists() and window.editor.actual_canvas.winfo_exists()
    if args.verify_window:
        window.update()
        controls=[]
        def visit(widget):
            for child in widget.winfo_children():
                try:label=child.cget('text')
                except Exception:label=''
                if label:controls.append(label)
                visit(child)
        visit(window)
        canvases=[dict(width=c.winfo_width(),height=c.winfo_height(),visible=bool(c.winfo_ismapped()))
            for c in (window.editor.canvas,window.editor.actual_canvas)]
        assert all(c['visible'] and c['width']>200 and c['height']>180 for c in canvases),canvases
        assert {'连接','断开','校零','平衡','停止','急停','设置','手绘','实际','清空','导出'}.issubset(controls)
        assert window.owner is None
        result=dict(passed=True,title=window.title(),window_size=[width,height],canvases=canvases,
            labels=controls,python=sys.executable,app_source=app.__file__,tk_library=window.tk.eval('set tk_library'),
            tcl_library=window.tk.eval('info library'),automatic_connection=False,hardware_actions=0)
        window.editor.destroy();window.destroy();print(json.dumps(result,ensure_ascii=True),flush=True)
        return
    window.mainloop()

if __name__=='__main__':
    try:main()
    except Exception:
        error=traceback.format_exc()
        try:(ROOT/'startup_error.log').write_text(error,'utf-8')
        except Exception:pass
        if '--verify-window' in sys.argv:raise
        ctypes.windll.user32.MessageBoxW(None,
            '手绘窗口未能启动。请完整解压整个压缩包，再使用最外层的“打开手绘窗口.cmd”。\n\n详细信息：DS13_App\\startup_error.log',
            'J280 手绘窗口启动失败',0x10)
        raise SystemExit(1)
