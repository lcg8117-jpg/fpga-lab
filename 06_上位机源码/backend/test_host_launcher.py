import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

LAUNCHER = Path(__file__).resolve().parents[1] / 'launch_host.py'


class HostLauncherTests(unittest.TestCase):
    def launcher(self):
        self.assertTrue(LAUNCHER.is_file(), '需要在启动入口修复虚拟环境的 Tcl/Tk 路径')
        spec = importlib.util.spec_from_file_location('host_launcher', LAUNCHER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_selected_python_can_initialize_tcl_without_global_environment_changes(self):
        self.launcher()
        environment = dict(os.environ)
        environment.pop('TCL_LIBRARY', None)
        environment.pop('TK_LIBRARY', None)
        code = (
            'import tkinter, json; from launch_host import configure_tk_runtime; '
            'configure_tk_runtime(); t=tkinter.Tcl(); '
            'print(json.dumps({"version":t.eval("info patchlevel"),"library":t.eval("info library")}))'
        )
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code],
                                cwd=LAUNCHER.parent, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = json.loads(result.stdout)
        self.assertTrue((Path(runtime['library']) / 'init.tcl').is_file())
        self.assertTrue(runtime['version'])

    def test_missing_bundled_libraries_do_not_replace_explicit_configuration(self):
        module = self.launcher()
        environment = {'TCL_LIBRARY':'custom Tcl', 'TK_LIBRARY':'custom Tk'}
        with tempfile.TemporaryDirectory() as base:
            module.configure_tk_runtime(base_prefix=base, environ=environment)
        self.assertEqual(environment, {'TCL_LIBRARY':'custom Tcl', 'TK_LIBRARY':'custom Tk'})

    def test_host_help_passes_through_for_both_variants_without_starting_devices(self):
        self.launcher()
        for variant in ('gesture', 'adaptive'):
            with self.subTest(variant=variant):
                result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(LAUNCHER), variant, '--help'],
                                        capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--offline-preview', result.stdout)
                self.assertIn('--headless', result.stdout)

    def test_adb1_help_does_not_open_hardware(self):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(LAUNCHER), 'ADB1', '--help'],
                                capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--demo', result.stdout)
        self.assertIn('--port', result.stdout)

    def test_v4_ble_option_is_scoped_to_v4_and_works_headless(self):
        for variant, expected in (('V4', 0), ('V3', 2)):
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(LAUNCHER),
                                     variant, '--v4-ble', '--headless', '--once', '--port', '0'],
                                    capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected == 0:
                self.assertFalse(json.loads(result.stdout)['connected'])

    def test_v4_ble_window_defaults_without_connecting(self):
        from host.v4_ble import configure
        class Var:
            def __init__(self): self.value = None
            def set(self, value): self.value = value
            def __str__(self): return 'route-mode'
        class Combo:
            def winfo_children(self): return []
            def cget(self, name): return 'route-mode'
            def configure(self, **kwargs): self.values = kwargs['values']
        class Notebook:
            def winfo_children(self): return []
            def tabs(self): return ['tab']
            def tab(self, index, name): return '通信'
            def select(self, index): self.selected = index
        combo, notebook = Combo(), Notebook()
        window = Mock()
        window.winfo_children.return_value = [combo, notebook]
        ui = Mock()
        for name in ('route_mode', 'port', 'ble_address', 'identity', 'notice'):
            setattr(ui, name, Var())
        app = Mock(FIRMWARE={4:0x46443434}, SCHEMAS={4:0x46443404})
        with patch.object(configure.ttk, 'Combobox', Combo), patch.object(configure.ttk, 'Notebook', Notebook), patch.object(configure, 'install') as install:
            configure.configure_window(window, ui, app)
        self.assertEqual(ui.route_mode.value, 'BLE')
        self.assertEqual(ui.port.value, '')
        self.assertEqual(ui.ble_address.value, configure.DEFAULT_ADDRESS)
        self.assertEqual(combo.values, ('BLE', 'USB+BLE', 'USB'))
        self.assertEqual(notebook.selected, 0)
        install.assert_called_once_with(configure.DEFAULT_ADDRESS)
        ui.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
