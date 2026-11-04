"""Portable entry: always disconnected, no automatic parameter write or RUN."""
from pathlib import Path
import sys,json,argparse
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'host/vendor')]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    import app
    if args.smoke:
        def forbidden(*a,**kw):raise AssertionError('Smoke attempted serial connection')
        app.SerialOwner=forbidden
    window=app.App()
    if args.smoke:
        window.withdraw();window.update_idletasks()
        assert window.owner is None
        assert window.editor.actual_canvas is not window.editor.canvas
        assert not window.port.get()
        from protocol import S1_BITS,gains_payload,DEFAULT_GAINS
        import struct
        assert struct.unpack('<6I',gains_payload(DEFAULT_GAINS))==S1_BITS
        (ROOT/'reports/RUNTIME_SMOKE.json').write_text(json.dumps(dict(passed=True,python=sys.version,disconnected=True,
            automatic_parameter_write=False,automatic_ZERO_RUN=False,hardware_actions=0),ensure_ascii=False,indent=2),encoding='utf-8')
        window.editor.destroy();window.destroy();print('PASS portable runtime: disconnected, S1 bits exact, two plot windows');return
    window.mainloop()
if __name__=='__main__':main()
