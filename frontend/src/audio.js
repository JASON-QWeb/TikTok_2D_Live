import { mouthShape } from './portrait/articulation.js';

export class ReplyCursor {
  constructor() { this.reset(); }
  reset() { this.initialized = false; this.last = 0; }
  take(replies) {
    if (!this.initialized) {
      this.initialized = true;
      this.last = Math.max(0, ...replies.map(r => r.seq));
      return [];
    }
    const fresh = replies.filter(r => r.seq > this.last).sort((a, b) => a.seq - b.seq);
    if (fresh.length) this.last = fresh.at(-1).seq;
    return fresh;
  }
}

export class SpeechPlayer {
  constructor(api, onSpeak, onError) {
    this.api = api; this.onSpeak = onSpeak; this.onError = onError;
    this.cursor = new ReplyCursor();
    this.queue = []; this.ready = new Map();
    this.generation = 0; this.unlocked = false; this.running = false;
    this.fetchController = new AbortController();
  }

  async unlock() {
    if (!this.context) {
      this.context = new AudioContext();
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.smoothingTimeConstant = 0.28;
      this.analyser.connect(this.context.destination);
      this.samples = new Float32Array(this.analyser.fftSize);
      this.spectrum = new Float32Array(this.analyser.frequencyBinCount);
    }
    await this.context.resume();
    this.unlocked = this.context.state === 'running';
    if (!this.unlocked) throw new Error('浏览器尚未允许音频，请再点击开启声音');
    this.prefetch(); void this.pump();
  }

  ingest(replies) {
    // final 与尾部文字会晚于音频发布；更新待播分段，不重新排队或重播。
    const updates = new Map(replies.map(item => [item.seq, item]));
    for (const item of this.queue) if (updates.has(item.seq)) Object.assign(item, updates.get(item.seq));
    if (this.current) {
      const update = updates.get(this.current.seq);
      if (update) { Object.assign(this.current, update); this.onSpeak(this.current, true); }
    }
    this.queue.push(...this.cursor.take(replies).map(r => ({ ...r })));
    if (this.queue.length > 30) {
      // 丢弃整组旧回复，保留当前正在讲话的后续分段，避免半句话突变。
      const keep = new Set(this.queue.slice(-15).map(r => r.reply_id));
      if (this.current) keep.add(this.current.reply_id);
      this.queue = this.queue.filter(r => keep.has(r.reply_id));
      this.onError('待播内容过多，已跳过旧回复');
    }
    this.prefetch(); void this.pump();
  }

  prefetch() {
    if (!this.unlocked) return;
    const next = this.queue.slice(0, 3);
    const ids = new Set(next.map(r => r.seq));
    for (const key of this.ready.keys()) if (!ids.has(key)) this.ready.delete(key);
    for (const item of next) {
      if (this.ready.has(item.seq) || item.has_audio === false) continue;
      const promise = this.api(`/audio?seq=${item.seq}`, { signal: this.fetchController.signal })
        .then(r => r.arrayBuffer()).then(data => this.context.decodeAudioData(data))
        .then(buffer => ({ buffer }), error => ({ error }));
      this.ready.set(item.seq, promise);
    }
  }

  async pump() {
    if (this.running || !this.unlocked) return;
    this.running = true;
    const generation = this.generation;
    try {
      while (this.queue.length && generation === this.generation) {
        this.prefetch();
        const item = this.queue.shift();
        const decoded = this.ready.get(item.seq);
        this.ready.delete(item.seq);
        this.prefetch();
        const result = decoded ? await decoded : {};
        if (generation !== this.generation) return;
        if (result.error) { this.onError(`语音播放失败：${result.error.message}`); continue; }
        this.current = item;
        this.onSpeak(item, true);
        if (result.buffer) await this.play(result.buffer);
        else await new Promise(resolve => setTimeout(resolve, 800));
        if (generation !== this.generation) return;
        this.onSpeak(item, false);
        this.current = null;
      }
    } finally { if (generation === this.generation) this.running = false; }
  }

  play(buffer) {
    return new Promise(resolve => {
      this.source = this.context.createBufferSource();
      const source = this.source;
      source.buffer = buffer; source.connect(this.analyser);
      source.onended = () => { source.disconnect(); if (this.source === source) this.source = null; resolve(); };
      source.start();
    });
  }

  level() {
    if (!this.source || !this.analyser) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const value of this.samples) sum += value * value;
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 5);
  }

  articulation(level) {
    if (!this.source || level < 0.065) return undefined;
    this.analyser.getFloatFrequencyData(this.spectrum);
    return mouthShape(level, this.spectrum, this.context.sampleRate / this.analyser.fftSize);
  }

  reset() {
    this.generation++;
    this.source?.stop(); this.source = null;
    this.fetchController.abort(); this.fetchController = new AbortController();
    this.queue.length = 0; this.ready.clear(); this.cursor.reset();
    this.current = null; this.running = false;
  }

  dispose() { this.reset(); void this.context?.close(); }
}
