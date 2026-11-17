export function createApi(token) {
  return async (url, options = {}) => {
    const headers = new Headers(options.headers);
    if (token) headers.set('X-Bridge-Token', token);
    const response = await fetch(url, { ...options, headers, cache: 'no-store' });
    if (!response.ok) throw new Error(`请求失败 (${response.status})：${url.split('?')[0]}`);
    return response;
  };
}

export async function* readEvents(body, onActivity = () => {}) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      onActivity();
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n');
        if (data) yield JSON.parse(data);
      }
      if (buffer.length > 2 * 1024 * 1024) throw new Error('事件数据过大');
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export async function connect(api, onState, onConnection, signal) {
  while (!signal.aborted) {
    const stream = new AbortController();
    const abort = () => stream.abort();
    signal.addEventListener('abort', abort, { once: true });
    let watchdog;
    const activity = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => stream.abort(), 35000);
    };
    try {
      activity();
      const response = await api('/api/events', { signal: stream.signal });
      onConnection('已连接');
      for await (const state of readEvents(response.body, activity)) onState(state);
      if (!signal.aborted) throw new Error('连接中断');
    } catch (error) {
      if (signal.aborted) return;
      onConnection(`重连中 · ${error.message}`);
      // SSE 不可用时仍能取回状态；每轮只保留一个连接，没有轮询重叠。
      try { onState(await (await api('/api/state', { signal })).json()); } catch { /* 下轮重试 */ }
      await new Promise(resolve => setTimeout(resolve, 1500));
    } finally { clearTimeout(watchdog); signal.removeEventListener('abort', abort); stream.abort(); }
  }
}
