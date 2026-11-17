"""真实 HTTP 连接验证：SSE、鉴权、音频序号和 3D 路由。"""
import json
import os
import sys
import threading
import unittest
from unittest.mock import patch

import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge


class Http3DTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.BridgeServer(('127.0.0.1', 0), bridge.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_port
        cls.http = requests.Session()
        cls.http.trust_env = False

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def get(self, path):
        return self.http.get(self.url + path, timeout=5)

    def test_pages_and_config(self):
        self.assertIn('avatar-canvas', self.get('/').text)
        self.assertIn('l2d', self.get('/avatar2d').text)
        self.assertIn('morphs', self.get('/api/avatar').json())

    def test_old_audio_must_not_fall_back_to_latest(self):
        with patch.object(bridge, 'LATEST_AUDIO', __file__):
            self.assertEqual(self.get('/audio?seq=999999').status_code, 404)
        self.assertEqual(self.get('/audio?seq=nope').status_code, 400)

    def test_malformed_text_fields_return_400(self):
        response = self.http.post(self.url + '/api/danmaku', json={'content': ['not a string']}, timeout=5)
        self.assertEqual(response.status_code, 400)

    def test_sse_pushes_after_publication(self):
        with self.http.get(self.url + '/api/events', stream=True, timeout=5) as response:
            self.assertEqual(response.status_code, 200)
            lines = response.iter_lines(chunk_size=1)
            def next_state():
                for line in lines:
                    if line.startswith(b'data:'):
                        return json.loads(line[5:])
            state = next_state()
            self.assertIn('started_at', state)
            bridge.EVENTS.publish()
            self.assertIn('queue', next_state())

    def test_sse_and_avatar_config_require_read_token_remotely(self):
        with patch.object(bridge.Handler, '_is_loopback_client', return_value=False), \
                patch.object(bridge, 'BRIDGE_TOKEN', 'a-private-read-token'), \
                patch.object(bridge, 'TOKEN_B', b'a-private-read-token'):
            for endpoint in ['/api/events', '/api/avatar', '/avatar3d', '/avatar2d']:
                self.assertEqual(self.get(endpoint).status_code, 401, endpoint)
            self.assertEqual(self.get('/api/events?token=a-private-read-token').status_code, 401)
            response = self.http.get(self.url + '/api/events', headers={'X-Bridge-Token': 'a-private-read-token'}, stream=True, timeout=5)
            try:
                self.assertEqual(response.status_code, 200)
            finally:
                response.close()


if __name__ == '__main__':
    unittest.main()
