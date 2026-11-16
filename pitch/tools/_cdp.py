"""Minimal Chrome DevTools client (stdlib only): real-time screenshots, key presses and JS evaluation."""
import base64, json, os, socket, struct, subprocess, time, urllib.request

class Chrome:
    def __init__(self, profile, port=9333, size=(1920, 1080), scale=1, extra=()):
        self.port = port
        args = ['google-chrome', '--headless=new', '--no-sandbox', '--hide-scrollbars', f'--remote-debugging-port={port}',
                f'--user-data-dir={profile}', f'--window-size={size[0]},{size[1]}', f'--force-device-scale-factor={scale}',
                '--autoplay-policy=no-user-gesture-required', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
                '--allow-file-access-from-files', '--remote-allow-origins=*', *extra, 'about:blank']
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1))
                pages = [t for t in tabs if t.get('type') == 'page']
                if pages: ws = pages[0]['webSocketDebuggerUrl']; break
            except Exception:
                pass
            time.sleep(0.1)
        if not ws: raise RuntimeError('Chrome did not start')
        path = ws.split(f':{port}', 1)[1]
        self.sock = socket.create_connection(('127.0.0.1', port)); self.sock.settimeout(60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        buf = b''
        while b'\r\n\r\n' not in buf: buf += self.sock.recv(4096)
        if b' 101 ' not in buf.split(b'\r\n', 1)[0]: raise RuntimeError(buf[:200])
        self.rest = buf.split(b'\r\n\r\n', 1)[1]; self.id = 0; self.events = []
        self.cmd('Page.enable'); self.cmd('Runtime.enable')

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk: raise RuntimeError('socket closed')
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def _recv(self):
        data = b''
        while True:
            h = self._read(2); fin, op, ln = h[0] & 0x80, h[0] & 0x0f, h[1] & 0x7f
            if ln == 126: ln = struct.unpack('>H', self._read(2))[0]
            elif ln == 127: ln = struct.unpack('>Q', self._read(8))[0]
            payload = self._read(ln)
            if op == 8: raise RuntimeError('closed by browser')
            if op in (9, 10): continue
            data += payload
            if fin: return json.loads(data.decode())

    def cmd(self, method, **params):
        self.id += 1; mid = self.id
        raw = json.dumps({'id': mid, 'method': method, 'params': params}).encode()
        mask = os.urandom(4); head = bytearray([0x81])
        if len(raw) < 126: head.append(0x80 | len(raw))
        elif len(raw) < 65536: head.append(0x80 | 126); head += struct.pack('>H', len(raw))
        else: head.append(0x80 | 127); head += struct.pack('>Q', len(raw))
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
        while True:
            msg = self._recv()
            if msg.get('id') == mid:
                if 'error' in msg: raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get('result', {})
            self.events.append(msg)

    def nav(self, url, wait=0.4):
        self.cmd('Page.navigate', url=url); time.sleep(wait)

    def js(self, expr, await_promise=True):
        r = self.cmd('Runtime.evaluate', expression=expr, awaitPromise=await_promise, returnByValue=True)
        if 'exceptionDetails' in r: raise RuntimeError(json.dumps(r['exceptionDetails'])[:600])
        return r['result'].get('value')

    def key(self, key, code=None, vk=0):
        for t in ('keyDown', 'keyUp'):
            self.cmd('Input.dispatchKeyEvent', type=t, key=key, code=code or key, windowsVirtualKeyCode=vk)

    def shot(self, path, transparent=False):
        if transparent: self.cmd('Emulation.setDefaultBackgroundColorOverride', color={'r': 0, 'g': 0, 'b': 0, 'a': 0})
        r = self.cmd('Page.captureScreenshot', format='png')
        open(path, 'wb').write(base64.b64decode(r['data']))

    def console(self):
        out = []
        for e in self.events:
            if e.get('method') == 'Runtime.consoleAPICalled':
                out.append(e['params']['type'] + ': ' + ' '.join(str(a.get('value', a.get('description', ''))) for a in e['params']['args']))
            elif e.get('method') == 'Runtime.exceptionThrown':
                out.append('EXCEPTION: ' + json.dumps(e['params']['exceptionDetails'])[:400])
        return out

    def close(self):
        try: self.cmd('Browser.close')
        except Exception: pass
        try: self.proc.wait(timeout=5)
        except Exception: self.proc.kill()
