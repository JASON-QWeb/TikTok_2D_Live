import * as THREE from 'three';
import { createPlaceholder, disposeObject } from './placeholder.js';

const EXPRESSIONS = ['aa', 'blink', 'happy', 'sad', 'angry', 'surprised'];
const MOOD = { shy: 'happy', thinking: 'neutral' };

export class Avatar {
  constructor(object, config, vrm = null, animations = []) {
    this.object = object;
    this.vrm = vrm;
    this.wrapper = new THREE.Group();
    this.wrapper.add(object);
    this.wrapper.rotation.y = Number(config.rotationY) || 0;
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    if (!Number.isFinite(size.y) || size.y < 0.01) throw new Error('模型没有可见的三维几何体');
    const scale = 1.75 / size.y;
    const center = box.getCenter(new THREE.Vector3());
    object.position.sub(new THREE.Vector3(center.x, box.min.y, center.z));
    this.wrapper.scale.setScalar(scale);
    this.bones = this.findBones(config);
    this.morphs = this.findMorphs(config);
    this.level = 0;
    this.elapsed = 0;
    this.emotion = 'neutral';
    this.gestureUntil = 0;
    this.nextBlink = 2.5;
    this.blinkStart = -10;
    this.mixer = animations.length ? new THREE.AnimationMixer(object) : null;
    const idle = animations.find(clip => /idle/i.test(clip.name)) || animations[0];
    if (idle) this.mixer.clipAction(idle).play();
    // VRM 归一化骨架以 T-pose 为基准，先把双臂放下；GLB 保留作者的初始姿态。
    if (vrm && !idle) {
      this.bones.leftUpperArm?.node.rotation.set(0, 0, -1.15);
      this.bones.rightUpperArm?.node.rotation.set(0, 0, 1.15);
      for (const bone of Object.values(this.bones)) bone.base.copy(bone.node.quaternion);
    }
    this.motionManager = { stopAllMotions: () => { this.gestureUntil = 0; } };
  }

  findBones(config) {
    const bones = {};
    for (const [semantic, name] of Object.entries(config.bones || {})) {
      const node = this.vrm?.humanoid?.getNormalizedBoneNode(semantic) || this.object.getObjectByName(name);
      if (node) bones[semantic] = { node, base: node.quaternion.clone() };
    }
    return bones;
  }

  findMorphs(config) {
    const morphs = Object.fromEntries(EXPRESSIONS.map(key => [key, []]));
    this.object.traverse(node => {
      if (!node.morphTargetDictionary) return;
      for (const key of EXPRESSIONS) {
        const names = config.morphs?.[key] || [key];
        for (const name of names) {
          const index = node.morphTargetDictionary[name];
          if (index !== undefined) morphs[key].push([node, index]);
        }
      }
    });
    return morphs;
  }

  expression(name) { this.emotion = MOOD[name] || name; }
  motion() { this.gestureUntil = this.elapsed + 2; }

  pose(name, x, y, z) {
    const bone = this.bones[name];
    if (!bone) return;
    bone.node.rotateX(x); bone.node.rotateY(y); bone.node.rotateZ(z);
  }

  update(dt, level, thinking) {
    this.elapsed += dt;
    const t = this.elapsed;
    this.level = THREE.MathUtils.damp(this.level, level, 20, dt);
    // 每帧从基准开始，再叠加动画与程序动作；没有动画轨的骨骼也不会累积旋转。
    for (const bone of Object.values(this.bones)) bone.node.quaternion.copy(bone.base);
    this.mixer?.update(dt);
    if (t > this.nextBlink) { this.blinkStart = t; this.nextBlink = t + 3 + Math.random() * 2; }
    const blink = Math.max(0, 1 - Math.abs(t - this.blinkStart - 0.09) / 0.09);
    const mood = MOOD[this.emotion] || this.emotion;
    for (const key of EXPRESSIONS) {
      const value = key === 'aa' ? this.level : key === 'blink' ? blink : mood === key ? 0.6 : 0;
      if (this.vrm) this.vrm.expressionManager?.setValue(key, value);
      else for (const [node, index] of this.morphs[key]) node.morphTargetInfluences[index] = value;
    }
    this.pose('head', Math.sin(t * 1.1) * 0.025 + this.level * 0.04,
      Math.sin(t * 0.4) * 0.08, thinking ? 0.08 : Math.sin(t * 0.7) * 0.02);
    this.pose('spine', Math.sin(t * 1.6) * 0.008, Math.sin(t * 0.6) * 0.015, 0);
    const gesture = this.gestureUntil > t ? Math.sin(t * 7) * 0.22 : 0;
    this.pose('rightUpperArm', -this.level * 0.15 - Math.abs(gesture), 0, gesture);
    this.pose('leftUpperArm', -this.level * 0.08, 0, 0);
    this.vrm?.update(dt);
  }

  get capabilities() {
    return {
      mouth: !!this.vrm?.expressionManager?.getExpression('aa') || this.morphs.aa.length > 0,
      blink: !!this.vrm?.expressionManager?.getExpression('blink') || this.morphs.blink.length > 0,
      head: !!this.bones.head,
    };
  }

  dispose() {
    this.mixer?.stopAllAction();
    this.mixer?.uncacheRoot(this.object);
    this.wrapper.removeFromParent();
    disposeObject(this.object);
  }
}

export function placeholderAvatar(look, config) {
  return new Avatar(createPlaceholder(look), config);
}

export async function loadAvatar(input, config) {
  const [{ GLTFLoader }, { VRMLoaderPlugin, VRMUtils }] = await Promise.all([
    import('three/addons/loaders/GLTFLoader.js'), import('@pixiv/three-vrm'),
  ]);
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (/^(data:|blob:)/.test(url)) return url;
    const resolved = new URL(url, location.origin);
    if (resolved.origin !== location.origin || !resolved.pathname.startsWith('/static/models/')) {
      throw new Error('模型外部资源必须放在 static/models/ 下；推荐导出自包含 GLB / VRM');
    }
    return resolved.href;
  });
  const loader = new GLTFLoader(manager);
  loader.register(parser => new VRMLoaderPlugin(parser));
  let gltf;
  if (input instanceof File) {
    if (!/\.(glb|vrm)$/i.test(input.name)) throw new Error('请选择 .glb 或 .vrm 文件');
    if (input.size > 100 * 1024 * 1024) throw new Error('模型请控制在 100 MB 以内');
    gltf = await loader.parseAsync(await input.arrayBuffer(), '/static/models/');
  } else {
    gltf = await loader.loadAsync(input);
  }
  const vrm = gltf.userData.vrm;
  if (vrm) {
    VRMUtils.rotateVRM0(vrm);
    VRMUtils.removeUnnecessaryVertices(vrm.scene);
    VRMUtils.combineSkeletons(vrm.scene);
  }
  try { return new Avatar(vrm?.scene || gltf.scene, config, vrm, gltf.animations); }
  catch (error) { disposeObject(gltf.scene); throw error; }
}
