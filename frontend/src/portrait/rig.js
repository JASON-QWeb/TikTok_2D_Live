import * as THREE from 'three';
import { fragmentShader, vertexShader } from './shaders.js';

export const PORTRAIT_VIEW = { height: 1.7, target: 1.105, distance: 1.93, min: 1.59, max: 2.03 };
const REGIONS = ['mouth', 'left', 'right', 'round', 'wide', 'smile'];
const { damp, smoothstep } = THREE.MathUtils;

function makeMesh(rig, photo, atlas) {
  const [width, height] = rig.size;
  const [columns, rows] = rig.grid;
  const position = [], uv = [], indices = [];
  for (let row = 0; row <= rows; row++) {
    for (let column = 0; column <= columns; column++) {
      const u = column / columns, v = row / rows;
      const i = row * (columns + 1) + column;
      const z = rig.depth[i] / 10000;
      // Inverse-project at the neutral camera so the depth surface preserves the original face.
      const scale = 1 - z / PORTRAIT_VIEW.distance;
      const x = (u - 0.5) * PORTRAIT_VIEW.height * width / height;
      const y = (1 - v) * PORTRAIT_VIEW.height;
      position.push(x * scale, PORTRAIT_VIEW.target + (y - PORTRAIT_VIEW.target) * scale, z);
      uv.push(u, 1 - v);
      if (row < rows && column < columns) {
        const below = i + columns + 1;
        indices.push(i, below, i + 1, i + 1, below, below + 1);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  geometry.setIndex(indices);
  const regions = REGIONS.map(name => rig.regions[name] || rig.regions.mouth);
  const uniforms = {
    photo: { value: photo }, atlas: { value: atlas },
    photoSize: { value: new THREE.Vector2(...rig.size) },
    atlasSize: { value: new THREE.Vector2(...rig.atlasSize) },
    regions: { value: regions.map(({ rect }) => new THREE.Vector4(...rect)) },
    strips: { value: regions.map(({ start, stride }) => new THREE.Vector3(...start, stride)) },
    frameCount: { value: rig.frames },
    neck: { value: (1 - rig.neck) * PORTRAIT_VIEW.height },
    pose: { value: new THREE.Vector4() }, speech: { value: new THREE.Vector3() },
    smile: { value: 0 },
    lids: { value: new THREE.Vector2() }, gaze: { value: new THREE.Vector2() },
  };
  const material = new THREE.ShaderMaterial({
    uniforms, vertexShader, fragmentShader,
    transparent: true, depthWrite: false, toneMapped: false,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false;
  return mesh;
}

function blinkAt(elapsed) {
  if (elapsed < 0 || elapsed >= 0.21) return 0;
  return smoothstep(elapsed, 0, 0.071) * (1 - smoothstep(elapsed, 0.096, 0.21));
}

class PortraitRig {
  constructor(rig, photo, atlas) {
    this.isPortrait = true;
    this.wrapper = makeMesh(rig, photo, atlas);
    this.capabilities = { mouth: true, blink: true, head: true };
    this.shape = this.wrapper.material.uniforms;
    this.hasSmile = !!rig.regions.smile;
    this.clock = 0;
    this.lastBlink = -2;
    this.nextBlink = 1.9 + Math.random() * 1.4;
    this.nextLook = 1.2;
    this.look = 0;
    this.gestureAt = -2;
    this.warmth = 0;
    this.breath = 0;
    this.emotion = 'neutral';
    this.motionManager = { stopAllMotions: () => { this.gestureAt = -2; } };
  }

  expression(name) { this.emotion = name; }
  motion() { this.gestureAt = this.clock; }

  update(dt, level, thinking, articulation) {
    this.clock += dt;
    const t = this.clock;
    const voice = this.shape.speech.value;
    const open = articulation?.open ?? 0;
    voice.x = damp(voice.x, open, open > voice.x ? 34 : 29, dt);
    voice.y = damp(voice.y, articulation?.round ?? 0, 19, dt);
    voice.z = damp(voice.z, articulation?.wide ?? 0, 19, dt);
    this.warmth = damp(this.warmth, this.emotion === 'happy' || this.emotion === 'shy' ? 1 : 0, 4, dt);
    this.breath = damp(this.breath, level > 0.07 ? 1 : 0, 2.8, dt);
    const smile = Math.max(this.warmth * 0.84, this.breath * 0.37);
    if (this.hasSmile) this.shape.smile.value = damp(this.shape.smile.value, smile, 5, dt);
    this.updateEyes(t, dt, thinking);
    const nodClock = t - this.gestureAt;
    const nod = nodClock < 0.76 ? Math.sin(nodClock * Math.PI / 0.76) * 0.067 : 0;
    const pose = this.shape.pose.value;
    pose.x = damp(pose.x, 0.042 * Math.sin(t * 0.7) + this.look * 0.032, 4, dt);
    pose.y = damp(pose.y, 0.022 * Math.sin(t * 0.91 + 0.7) + voice.x * 0.023 + nod - this.breath * 0.032, 5, dt);
    pose.z = damp(pose.z, 0.021 * Math.sin(t * 0.44) + this.warmth * 0.031, 3, dt);
    pose.w = Math.sin(t * 1.53) * 0.82 + Math.sin(t * 0.62) * 0.18 + this.breath * 0.29;
  }

  updateEyes(t, dt, thinking) {
    if (t >= this.nextBlink) {
      this.lastBlink = t;
      this.nextBlink = t + 2.8 + Math.random() * 3.4;
    }
    if (t >= this.nextLook) {
      this.look = Math.random() < 0.65 ? 0 : Math.random() * 1.8 - 0.9;
      this.nextLook = t + 1.2 + Math.random() * 3.3;
    }
    const squint = 0.1 * this.warmth;
    this.shape.lids.value.set(
      Math.max(squint, blinkAt(t - this.lastBlink)),
      Math.max(squint * 0.8, blinkAt(t - this.lastBlink - 0.016)),
    );
    const gaze = this.shape.gaze.value;
    gaze.x = damp(gaze.x, thinking ? 2.6 : this.look * 2.2, 20, dt);
    gaze.y = damp(gaze.y, thinking ? 0.8 : 0, 16, dt);
  }

  dispose() {
    const { geometry, material } = this.wrapper;
    this.wrapper.removeFromParent();
    material.uniforms.atlas.value.dispose();
    material.uniforms.photo.value.dispose();
    material.dispose();
    geometry.dispose();
  }
}

function validRig(rig) {
  if (rig.version !== 1 || !Array.isArray(rig.grid) || rig.grid.length !== 2) return false;
  const [width, height] = rig.grid;
  return Number.isInteger(width) && Number.isInteger(height)
    && width >= 2 && width <= 128 && height >= 2 && height <= 192
    && rig.depth?.length === (width + 1) * (height + 1)
    && rig.size?.length === 2 && rig.atlasSize?.length === 2
    && Number.isFinite(rig.neck) && rig.frames >= 2
    && ['mouth', 'left', 'right'].every(name => rig.regions?.[name]?.rect?.length === 4);
}

async function loadTexture(url) {
  const texture = await new THREE.TextureLoader().loadAsync(url.href);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.generateMipmaps = false;
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

export async function loadPortrait(manifest) {
  const url = new URL(manifest, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith('/static/portraits/')) {
    throw new Error('人物素材需放在 static/portraits/');
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error('找不到写实人物素材');
  const rig = await response.json();
  if (!validRig(rig)) throw new Error('人物网格配置不完整');
  const results = await Promise.allSettled(
    ['neutral.webp', 'expressions.webp'].map(name => loadTexture(new URL(name, url))),
  );
  if (results.some(result => result.status === 'rejected')) {
    for (const result of results) if (result.status === 'fulfilled') result.value.dispose();
    throw new Error('人物纹理加载失败');
  }
  return new PortraitRig(rig, ...results.map(result => result.value));
}
