#!/usr/bin/env python3
"""真实本地语音测量：写入明确的性能检查文本，不调用 LLM、不制造礼物数据。"""
import argparse
import json
import queue
import statistics
import threading
import time
from urllib.parse import urlsplit

import requests


def session():
    client = requests.Session()
    client.trust_env = False
    return client


def watch_events(url, states, ready, stop):
    with session() as client:
        try:
            with client.get(url + '/api/events', stream=True, timeout=(5, 20)) as response:
                response.raise_for_status()
                for line in response.iter_lines(chunk_size=1):
                    if stop.is_set():
                        return
                    if line.startswith(b'data:'):
                        states.put(json.loads(line[5:]))
                        ready.set()
        except Exception as error:
            states.put(error)
            ready.set()


def sample(client, url, mode, states, index):
    text = '这是本机语音链路的第%d次性能检查。' % (index + 1)
    before = client.get(url + '/api/state', timeout=5).json()
    previous = max([r['seq'] for r in before.get('replies', [])] or [0])
    start = time.perf_counter()
    result = client.post(url + '/api/danmaku', json={'kind': 'speak', 'text': text}, timeout=5)
    result.raise_for_status()
    if not result.json().get('queued'):
        raise RuntimeError('测试文本未入队，请确认没有其他任务占用服务')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if mode == 'sse':
            state = states.get(timeout=20)
            if isinstance(state, Exception):
                raise state
        else:
            time.sleep(0.5)  # 上游 avatar.html 的 500ms 轮询周期
            state = client.get(url + '/api/state', timeout=5).json()
        if state.get('status') == 'error':
            raise RuntimeError(state['error'])
        matches = [r for r in state.get('replies', []) if r.get('content') == text and r['seq'] > previous]
        if matches:
            response = client.get(url + '/audio', params={'seq': matches[-1]['seq']}, timeout=10)
            response.raise_for_status()
            if response.content[:4] != b'RIFF':
                raise RuntimeError('返回的不是 WAV')
            return {'seconds': round(time.perf_counter() - start, 4),
                    'audio_bytes': len(response.content), 'timings': state.get('timings', {})}
    raise TimeoutError('等待语音超时')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--mode', choices=['poll', 'sse'], default='sse')
    parser.add_argument('--count', type=int, default=5)
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in ('127.0.0.1', 'localhost'):
        parser.error('只支持本机性能检查')
    if not 1 <= args.count <= 20:
        parser.error('count 必须在 1 到 20 之间')
    states, ready, stop = queue.Queue(), threading.Event(), threading.Event()
    if args.mode == 'sse':
        threading.Thread(target=watch_events, args=(args.url, states, ready, stop), daemon=True).start()
        if not ready.wait(10):
            raise TimeoutError('SSE 未连接')
    try:
        with session() as client:
            samples = [sample(client, args.url, args.mode, states, i) for i in range(args.count)]
        print(json.dumps({'mode': args.mode, 'samples': samples,
                          'median_seconds': statistics.median(s['seconds'] for s in samples)}, ensure_ascii=False, indent=2))
    finally:
        stop.set()


if __name__ == '__main__':
    main()
