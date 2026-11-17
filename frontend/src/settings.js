import { DEFAULT_LOOK, exportPlaceholder } from './placeholder.js';
import { placeholderAvatar, loadAvatar } from './avatar.js';

const KEY = 'avatar3d-settings-v1';
const $ = id => document.getElementById(id);

export function readSettings() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

export function saveSettings(value) {
  try { localStorage.setItem(KEY, JSON.stringify(value)); } catch { /* 私密窗口仍可使用本次设置 */ }
}

export function bindSettings(stage, config, effects, notice) {
  const settings = readSettings();
  let look = { ...DEFAULT_LOOK, ...settings.look };
  let loading = 0;
  let custom = false;
  function attach(avatar, name, type) {
    stage.setAvatar(avatar); effects.setAvatar(avatar);
    $('avatar-name').textContent = name;
    $('model-type').textContent = type;
    const missing = Object.entries(avatar.capabilities).filter(([, value]) => !value)
      .map(([key]) => ({ mouth: '口型', blink: '眨眼', head: '头部骨骼' })[key]);
    $('model-info').textContent = missing.length ? `未检测到${missing.join('、')}，请检查模型映射` : '口型、眨眼、头部动作已就绪';
  }
  function generate() {
    loading++; custom = false;
    attach(placeholderAvatar(look, config), '简易人形', '程序生成 · GLB 可导出');
    settings.look = look; saveSettings(settings);
  }
  async function load(input, name) {
    const request = ++loading;
    $('model-info').textContent = '正在加载模型…';
    try {
      const avatar = await loadAvatar(input, config);
      if (request !== loading) { avatar.dispose(); return; }
      custom = true;
      attach(avatar, name, avatar.vrm ? 'VRM 人形' : 'GLB 模型');
      if (input instanceof File) notice('模型已载入本次预览。OBS 与刷新后使用的模型请在 config/avatar3d.json 中设置。');
    } catch (error) {
      if (request !== loading) return;
      $('model-info').textContent = '模型加载失败，已保留当前形象';
      notice(error.message);
    }
  }
  for (const key of ['skin', 'hair', 'outfit']) {
    $(key).value = look[key];
    $(key).addEventListener('input', () => { look = { ...look, [key]: $(key).value }; });
  }
  $('generate').addEventListener('click', generate);
  $('model-file').addEventListener('change', event => {
    const file = event.target.files[0]; if (file) void load(file, file.name); event.target.value = '';
  });
  $('export').addEventListener('click', async () => {
    const button = $('export'); button.disabled = true;
    try {
      const data = await exportPlaceholder(look);
      const url = URL.createObjectURL(new Blob([data], { type: 'model/gltf-binary' }));
      const link = document.createElement('a'); link.href = url; link.download = 'starter-avatar.glb'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (custom) notice('已导出颜色面板对应的简易形象；导入的真人模型未被修改。');
    } catch (error) { notice(`导出失败：${error.message}`); }
    finally { button.disabled = false; }
  });
  bindView(stage, settings);
  generate();
  if (config.url) void load(config.url, config.name || '3D 数字人');
  return { load };
}

function bindView(stage, settings) {
  const query = new URLSearchParams(location.search);
  const apply = {
    background: value => { $('stage').dataset.background = value; },
    camera: value => stage.frameCamera(value),
    quality: value => stage.quality(value),
  };
  for (const key of Object.keys(apply)) {
    const select = $(key);
    const value = query.get(key) || settings[key] || select.value;
    if ([...select.options].some(option => option.value === value)) select.value = value;
    apply[key](select.value);
    select.addEventListener('change', () => {
      settings[key] = select.value; saveSettings(settings); apply[key](select.value);
    });
  }
}
