import http.client
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'))
import vm_service as gateway

class SocketHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost',timeout=5)
        self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.connect(self.path)

class GatewayTests(unittest.TestCase):
    def test_health_via_unix_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'sock')
            server=gateway.ThreadingUnixServer(path,gateway.ApiHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                conn=SocketHTTP(path)
                conn.request('GET','/v1/health')
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                self.assertIn(b'0.1.0',response.read())
                conn.close()
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)

    def test_vnc_console_is_duplex_and_local_only(self):
        left,right=socket.socketpair()
        try:
            with patch.object(gateway.core,'virsh',return_value='vnc://127.0.0.1:5909'), \
                patch.object(gateway.core,'safe_name',return_value='testvm'), \
                patch.object(gateway.socket,'create_connection',return_value=left):
                c=gateway.Console('testvm','vnc')
                c.start()
                right.sendall(b'RFB 003.008\n')
                self.assertEqual(c.recv(),b'RFB 003.008\n')
                c.write(b'client-version')
                self.assertEqual(right.recv(1024),b'client-version')
                c.close()
                self.assertTrue(c.closed)
        finally:right.close();left.close()

    def test_public_host_vnc_refused(self):
        with patch.object(gateway.core,'virsh',return_value='vnc://0.0.0.0:5900'):
            with self.assertRaises(gateway.core.VmError):
                gateway.Console('testvm','vnc')

if __name__=='__main__':unittest.main()
