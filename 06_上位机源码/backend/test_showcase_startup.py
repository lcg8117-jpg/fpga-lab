import importlib.util
from pathlib import Path
import socket
import unittest


class ShowcaseStartupTests(unittest.TestCase):
    def checker(self):
        path = Path(__file__).resolve().parents[1] / 'check_showcase_startup.py'
        self.assertTrue(path.is_file(), '启动前应检查旧服务占用端口，防止新后台启动失败而网页继续使用旧接口')
        spec = importlib.util.spec_from_file_location('showcase_startup', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_free_port_allows_start(self):
        module = self.checker()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.assertEqual(module.occupied_ports([port]), [])

    def test_occupied_port_is_reported_without_touching_existing_service(self):
        module = self.checker()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.assertEqual(module.occupied_ports([port]), [port])
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                client, _ = listener.accept()
                client.close()


if __name__ == '__main__':
    unittest.main()
