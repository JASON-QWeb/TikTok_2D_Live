import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PORTRAIT_VIEW } from './portrait/rig.js';

export class Stage {
  constructor(canvas, config, speech, onStats) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.05, 50);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.minDistance = 0.5;
    this.controls.maxDistance = 8;
    this.controls.target.set(0, 1.13, 0);
    this.camera.position.set(0, 1.25, 3.6);
    this.scene.add(new THREE.HemisphereLight(0xe9f2ff, 0x6f5a4e, 2.1));
    this.light(0xffe5d1, 3.0, [2, 3, 3]);
    this.light(0xb9d7ff, 1.7, [-2, 2, 1]);
    this.light(0xffeee2, 3.5, [1, 3, -2]);
    this.speech = speech;
    this.cameraMode = 'portrait';
    this.onStats = onStats;
    this.defaults = [Math.max(0.5, Math.min(2, config.maxPixelRatio || 1.5)),
      Math.max(15, Math.min(60, config.maxFps || 60))];
    [this.pixelRatio, this.maxFps] = this.defaults;
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(canvas.parentElement);
    this.last = 0; this.statStart = 0; this.frames = 0;
    this.loop = time => { this.frame = requestAnimationFrame(this.loop); this.render(time); };
    this.frame = requestAnimationFrame(this.loop);
  }

  light(color, intensity, position) {
    const light = new THREE.DirectionalLight(color, intensity);
    light.position.set(...position);
    this.scene.add(light);
  }

  resize() {
    const { width, height } = this.canvas.parentElement.getBoundingClientRect();
    const oldAspect = this.camera.aspect;
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, this.pixelRatio));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / Math.max(1, height);
    this.camera.updateProjectionMatrix();
    if (this.avatar?.isPortrait && Math.abs(oldAspect - this.camera.aspect) > 0.01) {
      this.frameCamera(this.cameraMode);
    }
  }

  setAvatar(avatar) {
    this.avatar?.dispose();
    this.avatar = avatar;
    this.scene.add(avatar.wrapper);
    this.canvas.parentElement.dataset.renderer = avatar.isPortrait ? 'portrait' : 'model';
    this.frameCamera(this.cameraMode);
  }

  frameCamera(mode) {
    this.cameraMode = mode;
    const portrait = this.avatar?.isPortrait;
    this.controls.enablePan = !portrait;
    this.controls.minAzimuthAngle = portrait ? -0.13 : -Infinity;
    this.controls.maxAzimuthAngle = portrait ? 0.13 : Infinity;
    this.controls.minPolarAngle = portrait ? Math.PI / 2 - 0.07 : 0;
    this.controls.maxPolarAngle = portrait ? Math.PI / 2 + 0.07 : Math.PI;
    if (portrait) {
      // Preserve hair clearance in vertical streams while keeping the torso below the crop.
      const fit = Math.max(1, Math.min(1.26, 0.67 / this.camera.aspect));
      const target = PORTRAIT_VIEW.target - (fit - 1) * 0.55 + (mode === 'close' ? 0.085 : 0);
      const distance = (mode === 'close' ? PORTRAIT_VIEW.min + 0.05 : PORTRAIT_VIEW.distance) * fit;
      this.controls.minDistance = PORTRAIT_VIEW.min * fit;
      this.controls.maxDistance = PORTRAIT_VIEW.max * fit;
      this.controls.target.set(0, target, 0);
      this.camera.position.set(0, target, distance);
      this.controls.update();
      return;
    }
    this.controls.minDistance = 0.5;
    this.controls.maxDistance = 8;
    this.controls.target.set(0, mode === 'portrait' ? 1.38 : 0.92, 0);
    this.camera.position.set(0, mode === 'portrait' ? 1.42 : 1.04, mode === 'portrait' ? 1.65 : 3.65);
    this.controls.update();
  }

  quality(mode) {
    [this.pixelRatio, this.maxFps] = mode === 'low' ? [1, 30] : mode === 'high' ? [2, 60] : this.defaults;
    this.resize();
  }

  render(time) {
    const interval = 1000 / this.maxFps;
    const deadline = this.nextFrame ?? time;
    if (time < deadline - 1) return;
    this.nextFrame = Math.max(deadline + interval, time + interval * 0.5);
    const dt = Math.min(0.1, (time - (this.last || time)) / 1000);
    this.last = time;
    if (this.avatar) {
      const level = this.speech.level();
      const articulation = this.avatar.isPortrait ? this.speech.articulation(level) : undefined;
      this.avatar.update(dt, level, this.thinking, articulation);
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.frames++;
    if (time - this.statStart >= 1000) {
      this.onStats(Math.round(this.frames * 1000 / (time - this.statStart)));
      this.frames = 0; this.statStart = time;
    }
  }

  dispose() {
    cancelAnimationFrame(this.frame);
    this.observer.disconnect(); this.controls.dispose();
    this.avatar?.dispose(); this.renderer.dispose();
  }
}
