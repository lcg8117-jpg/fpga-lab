"""Launch an unchanged delivered Host with its Python's bundled Tcl/Tk libraries."""

import argparse
import importlib.util
import os
from pathlib import Path
import runpy
import sys


def configure_tk_runtime(*, base_prefix=None, environ=None):
    import _tkinter

    environment = os.environ if environ is None else environ
    runtime = Path(sys.base_prefix if base_prefix is None else base_prefix) / "tcl"
    for variable, directory, entry in (
        ("TCL_LIBRARY", f"tcl{_tkinter.TCL_VERSION}", "init.tcl"),
        ("TK_LIBRARY", f"tk{_tkinter.TK_VERSION}", "tk.tcl"),
    ):
        library = runtime / directory
        if not environment.get(variable) and (library / entry).is_file():
            environment[variable] = str(library)


def fd2_main(variant, arguments):
    import json
    import threading
    from host.fd2_bridge import Bridge, load_upstream, install, ROOT

    parser = argparse.ArgumentParser(
        description="FD2 V1-V5 Host, explicit connection only"
    )
    parser.add_argument("--port", type=int, default=8371, help="loopback HTTP port")
    parser.add_argument(
        "--offline-preview",
        action="store_true",
        help="blank detached Host; no hardware",
    )
    parser.add_argument(
        "--headless", action="store_true", help="HTTP only; never connects hardware"
    )
    parser.add_argument(
        "--once", action="store_true", help="print status and exit (headless only)"
    )
    parser.add_argument(
        "--replay", type=Path, help="ForestFive.Physical.v1 ZIP, RX-only"
    )
    parser.add_argument("--replay-speed", type=float, default=1.0)
    parser.add_argument("--v4-ble", action="store_true", help="V4 BT10 connection helper")
    options = parser.parse_args(arguments)
    if options.v4_ble and variant != 4:
        parser.error("--v4-ble is only available for V4")
    if options.once and not options.headless:
        parser.error("--once requires --headless")
    bridge = Bridge(variant, replay=bool(options.replay))
    server = bridge.serve(options.port)
    replay = None
    try:
        if options.replay:
            from host.fd2_replay import Replay

            replay = Replay(bridge, options.replay, speed=options.replay_speed).start()
        if options.once:
            if replay:
                replay.thread.join(timeout=2)
            print(json.dumps(bridge.status(), ensure_ascii=False))
            return
        print(
            f"FD2 V{variant}: http://127.0.0.1:{server.server_port} (disconnected until explicit Connect)",
            flush=True,
        )
        if options.headless:
            threading.Event().wait()
        else:
            configure_tk_runtime()
            import tkinter as tk

            if options.offline_preview or options.replay:
                # A separate detached display has no original Connect or hardware callbacks.
                from tkinter import ttk

                window = tk.Tk()
                window.title(
                    f"FD2 V{variant} — " + ("回放" if options.replay else "离线预览")
                )
                window.geometry("800x300")
                ttk.Label(
                    window,
                    text=(
                        "原始日志离线回放：硬件操作已禁用"
                        if options.replay
                        else "未连接：离线预览，无硬件访问"
                    ),
                    font=("Microsoft YaHei UI", 18),
                ).pack(pady=40)
                ttk.Label(
                    window, text=f"网页数据桥：http://127.0.0.1:{server.server_port}"
                ).pack()
                window.mainloop()
            else:
                modules = load_upstream()
                # This is a dedicated Host process. Keep generic upstream imports
                # isolated here; do not import the backend app module in this process.
                sys.path.insert(0, str(ROOT))
                for name, module in modules.items():
                    sys.modules[name] = module
                import importlib

                spec = importlib.util.spec_from_file_location(
                    "j280_fd2_upstream_app", ROOT / "app.py"
                )
                app = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(app)
                install(bridge, modules)
                app.enable_dpi_awareness()
                window = tk.Tk()
                instance = app.App(window, variant)
                if options.v4_ble:
                    from host.v4_ble.configure import configure_window
                    configure_window(window, instance, app)
                if variant == 1:
                    from host.fd2_v1_presets import install as install_v1_presets
                    install_v1_presets(instance)
                window.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if replay:
            replay.stop()
        server.shutdown()
        server.server_close()


def delivery_main(profile, arguments):
    """Start one delivered owner; offline headless mode never connects a device."""
    import json
    import threading
    parser = argparse.ArgumentParser(description='J280 最新交付配套 Host')
    parser.add_argument('--port', type=int, default=8371)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--replay', type=Path, help='G3/V4 ZIP 或 DS13 frames.bin 离线日志')
    parser.add_argument('--replay-speed', type=float, default=1.0)
    options = parser.parse_args(arguments)
    if options.once and not options.headless:
        parser.error('--once requires --headless')
    if options.replay:
        from host.delivery_replay import ReplayBridge, DS13ReplayBridge, V4DiagnosticReplayBridge
        if profile=='DS13': replay=DS13ReplayBridge(options.replay,speed=options.replay_speed)
        elif profile in ('V4_R28', 'V4_USB_BLE'): replay=V4DiagnosticReplayBridge(options.replay,speed=options.replay_speed,profile=profile)
        else: replay=ReplayBridge(profile,options.replay,speed=options.replay_speed)
        server=replay.serve(options.port)
        try:
            replay.start()
            if options.once:
                replay.thread.join(timeout=2)
                print(json.dumps(replay.status(),ensure_ascii=False))
            elif options.headless:
                threading.Event().wait()
            else:
                configure_tk_runtime()
                import tkinter as tk
                from tkinter import ttk
                window=tk.Tk();window.title(f'{profile} 离线回放');window.geometry('620x200')
                ttk.Label(window,text='原始日志只读回放；硬件操作已禁用').pack(pady=36)
                ttk.Label(window,text=f'网页数据桥：http://127.0.0.1:{server.server_port}').pack()
                window.mainloop()
        finally:
            replay.stop()
        return
    if profile == 'ADB1_07':
        from host.adb1_07_bridge import ADB107Bridge
        bridge = ADB107Bridge()
        def start_window():
            import tkinter as tk
            from host.adb1_07_window import create_window
            window = tk.Tk()
            create_window(window, Path(__file__).resolve().parent/'host/adb1_07/logs', bridge)
            window.mainloop()
    elif profile == 'ADB1_FASTP':
        from host.adb1_fastp_bridge import ADB1FastPBridge, install, load_upstream
        bridge = ADB1FastPBridge()
        def start_window():
            modules = load_upstream()
            install(bridge, modules)
            sys.argv = [sys.argv[0]]
            modules['app'].main()
    elif profile == 'V4_R28':
        from host.v4_r28_bridge import V4R28Bridge, install, ROOT
        bridge = V4R28Bridge()
        def start_window():
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(ROOT / 'vendor_ble'))
            import client, app
            install(bridge, client)
            app.main()
    elif profile == 'V4_USB_BLE':
        from host.v4_usb_ble_bridge import V4UsbBleBridge, install, load_upstream, ROOT
        bridge = V4UsbBleBridge()
        def start_window():
            modules = load_upstream()
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(ROOT / 'vendor_ble'))
            for name, module in modules.items():
                sys.modules[name] = module
            install(bridge, modules)
            import app
            app.main()
    elif profile == 'DS13':
        from host.ds13_bridge import DS13Bridge, install
        bridge = DS13Bridge()
        def start_window():
            from host.ds13_theme import apply_theme
            modules = install(bridge)
            window = modules['app'].App()
            apply_theme(window)
            window.mainloop()
    elif profile == 'G3_C1':
        from host.g3_c1_bridge import G3Bridge, start_window as start_g3_window
        bridge = G3Bridge()
        def start_window():
            start_g3_window(bridge)
    else:
        raise ValueError('未知交付版本')
    server = bridge.serve(options.port)
    try:
        if options.once:
            print(json.dumps(bridge.status(), ensure_ascii=False))
        elif options.headless:
            threading.Event().wait()
        else:
            configure_tk_runtime()
            start_window()
    finally:
        server.shutdown()
        server.server_close()


def main():
    if len(sys.argv) > 1 and sys.argv[1].upper() in ('ADB1_07', 'ADB1_FASTP', 'V4_R28', 'V4_USB_BLE', 'G3_C1', 'DS13'):
        return delivery_main(sys.argv[1].upper(), sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1].upper() == 'ADB1':
        configure_tk_runtime()
        root = Path(__file__).resolve().parent
        # ADB1 and FD2 use separate owners; reuse the bundled pyserial 3.5 only.
        sys.path.insert(0, str(root / 'host' / 'fd2' / 'upstream' / 'host' / 'vendor'))
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from host.adb1.app import main as adb1_main
        if '--help' in sys.argv or '-h' in sys.argv:
            return adb1_main()
        from host.adb1_bridge import ADB1Bridge, install as install_adb1_bridge
        bridge = ADB1Bridge()
        server = bridge.serve(8371)
        install_adb1_bridge(bridge)
        try:
            return adb1_main()
        finally:
            server.shutdown()
            server.server_close()
    if len(sys.argv) > 1 and sys.argv[1].upper() in ("V1", "V2", "V3", "V4", "V5"):
        return fd2_main(int(sys.argv[1][1]), sys.argv[2:])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("variant", choices=("gesture", "adaptive", "precise"))
    options, host_arguments = parser.parse_known_args()
    if (
        "--headless" not in host_arguments
        and "--help" not in host_arguments
        and "-h" not in host_arguments
    ):
        configure_tk_runtime()
    root = Path(__file__).resolve().parent
    host_root = (
        root
        / "host"
        / ("sp24/gesture" if options.variant == "precise" else options.variant)
    )
    entry = host_root / "host" / "swing_tune_host.py"
    sys.path.insert(0, str(entry.parent))
    sys.argv = [str(entry), *host_arguments]
    if options.variant == "precise":
        from host.sp24_bridge import install

        spec = importlib.util.spec_from_file_location("j280_sp24_host", entry)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        install(module)
        module.main()
    else:
        runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
