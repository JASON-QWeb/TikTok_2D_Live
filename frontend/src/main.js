import './style.css';
import { Stage } from './stage.js';
import { SpeechPlayer } from './audio.js';
import { createApi, connect } from './transport.js';
import { LiveEffects } from './live.js';
import { bindSettings } from './settings.js';

const $ = id => document.getElementById(id);
const query = new URLSearchParams(location.search);
const api = createApi(query.get('token') || '');
const controller = new AbortController();
let stage, effects, captionTimer;

function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }

const speech = new SpeechPlayer(api, (item, speaking) => {
  clearTimeout(captionTimer);
  $('caption').textContent = item.reply;
  if (speaking) {
    stage?.avatar?.expression(item.emotion || 'neutral');
    $('state-label').textContent = '正在说话';
  } else {
    $('state-label').textContent = '就绪';
    captionTimer = setTimeout(() => { $('caption').textContent = ''; }, item.final ? 2000 : 6000);
  }
}, notice);

let session = null, lastBridgeError = '';
function onState(state) {
  if (session !== null && state.started_at !== session) {
    speech.reset(); effects.reset(); $('caption').textContent = '';
  }
  session = state.started_at;
  speech.ingest(state.replies || []);
  effects.ingest(state);
  stage.thinking = state.status === 'processing';
  if (!speech.source) $('state-label').textContent = ({ idle: '等待弹幕', queued: '排队中', processing: '思考中', done: '就绪', error: '处理失败' })[state.status] || state.status;
  const t = state.timings || {};
  $('first-audio').textContent = t.first_audio == null ? '—' : `${t.first_audio.toFixed(2)} s`;
  $('queue-wait').textContent = t.waited == null ? '—' : `${t.waited.toFixed(2)} s`;
  $('queue-depth').textContent = state.queue?.depth ?? '—';
  $('tts-backend').textContent = state.tts_backend || '—';
  if (state.content) {
    const content = `${state.user ? `${state.user}：` : ''}${state.content}`;
    $('last-message').textContent = content; $('danmaku').textContent = content;
  }
  if (state.error) notice(lastBridgeError = state.error);
  else if (lastBridgeError && $('notice').textContent === lastBridgeError) {
    notice(''); lastBridgeError = '';
  }
  if (state.tts_backend === 'vidu') notice('3D 实时口型需要本地语音：请将 TTS_BACKEND 改为 auto / piper / say / sapi。');
}

async function unlock() {
  try {
    await speech.unlock(); effects.unlock();
    $('enable-audio').textContent = '声音已开启';
    $('enable-audio').disabled = true;
    if (query.has('obs')) $('enable-audio').hidden = true;
  } catch (error) { notice(error.message); }
}

function bindInterface() {
  $('enable-audio').addEventListener('click', unlock);
  $('message-form').addEventListener('submit', async event => {
    event.preventDefault();
    const text = $('message').value.trim();
    if (!text) return;
    const button = event.currentTarget.querySelector('button'); button.disabled = true;
    try {
      await unlock();
      const body = $('message-mode').value === 'speak' ? { kind: 'speak', text } : { user: '主播', content: text };
      const result = await (await api('/api/danmaku', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })).json();
      notice(result.queued ? '' : '这条内容被去重或队列已满，未加入队列');
      if (result.queued) $('message').value = '';
    } catch (error) { notice(error.message); }
    finally { button.disabled = false; }
  });
  const obs = new URL(location.href); obs.searchParams.set('obs', '1'); obs.searchParams.set('background', 'transparent');
  $('obs-link').href = obs.href;
  const legacy = new URL('/avatar2d', location.origin);
  if (query.has('token')) legacy.searchParams.set('token', query.get('token'));
  $('legacy-link').href = legacy.href;
  if (query.has('obs')) {
    document.body.classList.add('obs');
    document.documentElement.classList.add('obs');
    const audioButton = $('enable-audio'); audioButton.classList.add('audio-overlay');
    document.body.append(audioButton);
    // OBS 通常允许自动播放；普通浏览器仍保留点击解锁入口。
    void unlock();
  }
}

async function main() {
  try {
    const config = await (await api('/api/avatar')).json();
    stage = new Stage($('avatar-canvas'), config, () => speech.level(), fps => { $('fps').textContent = `${fps} FPS`; });
    effects = new LiveEffects(stage, notice);
    bindSettings(stage, config, effects, notice);
    bindInterface();
    void connect(api, onState, text => { $('connection').textContent = text; }, controller.signal);
  } catch (error) { notice(`启动失败：${error.message}`); $('model-type').textContent = '3D 启动失败'; }
}
window.addEventListener('pagehide', () => { controller.abort(); speech.dispose(); effects?.reset(); stage?.dispose(); });
void main();
