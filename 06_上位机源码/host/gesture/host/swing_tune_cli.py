"""Local assistant CLI. Talks only to the one Host, never directly to COM."""
import argparse
import json
import time
import urllib.request
import urllib.error
import uuid
from tune_parameters import RESTORE_FIELDS


def exchange(port, path, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data,
                                 headers={'X-J280-Local': '1', 'Content-Type': 'application/json'})
    # Never route local control traffic through ambient proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=4) as response:
        return json.load(response)


def main(argv=None):
    p = argparse.ArgumentParser(description='唯一Host本机桥；不打开串口，不自动重试命令')
    p.add_argument('--bridge-port', type=int, default=8371)
    p.add_argument('--request-id', help='不确定旧请求结果时复用原ID，不可换ID重放运动')
    p.add_argument('--wait', type=float, default=0, help='只轮询结果的秒数；不重发命令')
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('status', 'stop', 'run', 'zero', 'clear-fault', 'balance', 'disconnect'):
        sub.add_parser(name)
    con = sub.add_parser('connect')
    con.add_argument('port')
    con.add_argument('--firmware-swm2', action='store_true', required=True)
    setter = sub.add_parser('set')
    setter.add_argument('name')
    setter.add_argument('value', type=int)
    restore = sub.add_parser('restore-pid')
    for field in RESTORE_FIELDS:
        restore.add_argument('--'+field.replace('_', '-'), type=int, required=True)
    query = sub.add_parser('result')
    query.add_argument('id')
    a = p.parse_args(argv)
    try:
        if a.action == 'status':
            result = exchange(a.bridge_port, '/status')
        elif a.action == 'result':
            result = exchange(a.bridge_port, '/request/'+a.id)
        else:
            rid = a.request_id or str(uuid.uuid4())
            body = dict(action=a.action, request_id=rid)
            if a.action == 'connect':
                body.update(port=a.port, firmware_swm2=a.firmware_swm2)
            elif a.action == 'set':
                body.update(name=a.name, value=a.value)
            elif a.action == 'restore-pid':
                body['value'] = {field: getattr(a, field) for field in RESTORE_FIELDS}
            print(json.dumps({'request_id': rid, 'note': 'ID已确定；超时后查result，命令不会重发'}, ensure_ascii=False), flush=True)
            result = exchange(a.bridge_port, '/command', body)
            deadline = time.monotonic()+max(0, a.wait)
            while a.action not in ('connect', 'disconnect') and result.get('state') in ('queued', 'pending', 'accepted', 'swinging') and time.monotonic() < deadline:
                time.sleep(0.15)
                result = exchange(a.bridge_port, '/request/'+rid)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('state') in ('rejected', 'failed', 'indeterminate') else 0
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(json.dumps({'error': str(exc), 'note': '未重发。保留request_id，通过result查询；不要盲目重复RUN。'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
