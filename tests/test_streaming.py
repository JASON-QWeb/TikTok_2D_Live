"""确定性并发回归：测试替身只用于隔离网络与语音，不作为性能数据。"""
import sys
import os
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge
from speech_stream import prefetch, iter_sentences


class StreamingRegression(unittest.TestCase):
    def setUp(self):
        bridge.REPLIES.clear()

    def test_network_keeps_receiving_while_tts_runs(self):
        tts_started = threading.Event()
        received_rest = threading.Event()

        def source(*args, **kwargs):
            yield '这是一句完整的回复。下一句'
            self.assertTrue(tts_started.wait(2))
            yield '也能在语音合成时接收。'
            received_rest.set()

        def synthesize(text, voice):
            tts_started.set()
            self.assertTrue(received_rest.wait(2), 'TTS 不应阻塞网络读取')
            return ''

        with patch.object(bridge, 'deepseek_stream', source), patch.object(bridge, 'local_tts', synthesize):
            timings = {}
            reply, _ = bridge.stream_reply('测试', '', '', None, timings)
        self.assertIn('也能在语音合成时接收', reply)
        self.assertIn('deepseek', timings)
        self.assertIn('first_audio', timings)
        self.assertTrue(bridge.REPLIES[-1]['final'])

    def test_producer_error_propagates_and_next_request_still_runs(self):
        def broken():
            yield '第一段'
            raise RuntimeError('connection closed')
        stream = prefetch(broken())
        self.assertEqual(next(stream), '第一段')
        with self.assertRaisesRegex(RuntimeError, 'connection closed'):
            next(stream)
        self.assertEqual(list(prefetch(iter(['第二次']))), ['第二次'])

    def test_cancellation_releases_a_full_producer_queue(self):
        closed = threading.Event()
        def endless():
            try:
                while True:
                    yield 'text'
            finally:
                closed.set()
        stream = prefetch(endless(), capacity=1)
        next(stream)
        stream.close()
        self.assertTrue(closed.wait(2))
        self.assertEqual(list(prefetch(iter(['next']))), ['next'])

    def test_no_punctuation_has_bounded_tts_chunks(self):
        text = '这是很长的连续讲话' * 30
        parts = list(iter_sentences(iter([text])))
        self.assertEqual(''.join(parts), text)
        self.assertLessEqual(max(map(len, parts)), 48)


if __name__ == '__main__':
    unittest.main()
