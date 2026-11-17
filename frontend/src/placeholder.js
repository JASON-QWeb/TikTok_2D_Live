import * as THREE from 'three';

export const DEFAULT_LOOK = { skin: '#c98e6f', hair: '#30231f', outfit: '#334b49' };

function surface(color, roughness = 0.65) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 });
}

function ellipsoid(parent, name, position, scale, material) {
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), material);
  mesh.name = name;
  mesh.position.set(...position);
  mesh.scale.set(...scale);
  parent.add(mesh);
  return mesh;
}

function joint(parent, name, position) {
  const group = new THREE.Group();
  group.name = name;
  group.position.set(...position);
  parent.add(group);
  return group;
}

function morphY(mesh, name, multiplier) {
  const positions = mesh.geometry.attributes.position;
  const target = positions.clone();
  for (let i = 0; i < target.count; i++) target.setY(i, positions.getY(i) * multiplier);
  mesh.geometry.morphAttributes.position = [target];
  mesh.updateMorphTargets();
  mesh.morphTargetDictionary = { [name]: 0 };
}

function face(head, skin, hair) {
  ellipsoid(head, 'Face', [0, 0.065, 0], [0.105, 0.135, 0.097], skin);
  ellipsoid(head, 'Nose', [0, 0.04, 0.097], [0.017, 0.027, 0.026], skin);
  for (const side of [-1, 1]) {
    ellipsoid(head, `Ear${side}`, [side * 0.103, 0.05, 0], [0.018, 0.033, 0.022], skin);
    const eye = ellipsoid(head, `Eye${side}`, [side * 0.041, 0.085, 0.086],
      [0.025, 0.012, 0.011], surface('#ebe5dc', 0.3));
    morphY(eye, 'blink', 0.04);
    const iris = ellipsoid(head, `Iris${side}`, [side * 0.041, 0.085, 0.096],
      [0.009, 0.010, 0.004], surface('#302a25', 0.2));
    morphY(iris, 'blink', 0.04);
    ellipsoid(head, `Brow${side}`, [side * 0.042, 0.107, 0.088], [0.026, 0.004, 0.007], hair);
  }
  const mouth = ellipsoid(head, 'Mouth', [0, 0.003, 0.087], [0.029, 0.004, 0.01], surface('#743b37'));
  morphY(mouth, 'jawOpen', 6);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 20, 0, Math.PI * 2, 0, 1.55), hair);
  cap.name = 'Hair';
  cap.position.set(0, 0.081, -0.007);
  cap.scale.set(0.112, 0.131, 0.106);
  head.add(cap);
  ellipsoid(head, 'HairBack', [0, 0.033, -0.07], [0.108, 0.16, 0.052], hair);
}

export function createPlaceholder(look = DEFAULT_LOOK) {
  const root = new THREE.Group();
  root.name = 'GeneratedAvatar';
  root.userData.generator = 'TikTok 3D Live — procedural starter';
  const skin = surface(look.skin), hair = surface(look.hair), cloth = surface(look.outfit, 0.9);
  const pants = surface('#222b30', 0.95);
  const spine = joint(root, 'Spine', [0, 1.04, 0]);
  ellipsoid(spine, 'Torso', [0, 0.17, 0], [0.205, 0.28, 0.115], cloth);
  ellipsoid(spine, 'Collar', [0, 0.365, 0], [0.078, 0.055, 0.075], cloth);
  const neck = joint(spine, 'Neck', [0, 0.37, 0]);
  ellipsoid(neck, 'NeckMesh', [0, 0.035, 0], [0.048, 0.075, 0.05], skin);
  const head = joint(neck, 'Head', [0, 0.105, 0]);
  face(head, skin, hair);
  for (const side of [-1, 1]) {
    const arm = joint(spine, side > 0 ? 'LeftArm' : 'RightArm', [side * 0.18, 0.29, 0]);
    arm.rotation.z = side * 0.1;
    ellipsoid(arm, 'Sleeve', [side * 0.02, -0.12, 0], [0.064, 0.18, 0.067], cloth);
    ellipsoid(arm, 'Forearm', [side * 0.035, -0.345, 0.018], [0.042, 0.145, 0.045], skin);
    ellipsoid(arm, 'Hand', [side * 0.035, -0.49, 0.026], [0.043, 0.065, 0.026], skin);
    ellipsoid(root, 'Leg', [side * 0.093, 0.57, 0], [0.09, 0.43, 0.095], pants);
    ellipsoid(root, 'Shoe', [side * 0.093, 0.09, 0.035], [0.083, 0.065, 0.14], hair);
  }
  ellipsoid(root, 'Hips', [0, 0.96, 0], [0.187, 0.15, 0.11], pants);
  return root;
}

export async function exportPlaceholder(look) {
  const { GLTFExporter } = await import('three/addons/exporters/GLTFExporter.js');
  const model = createPlaceholder(look);
  try {
    return await new GLTFExporter().parseAsync(model, { binary: true });
  } finally { disposeObject(model); }
}

export function disposeObject(root) {
  const textures = new Set(), materials = new Set(), geometries = new Set();
  root.traverse(node => {
    if (node.geometry) geometries.add(node.geometry);
    for (const mat of [].concat(node.material || [])) {
      materials.add(mat);
      for (const value of Object.values(mat)) if (value?.isTexture) textures.add(value);
    }
  });
  textures.forEach(t => { t.source?.data?.close?.(); t.dispose(); });
  materials.forEach(m => m.dispose());
  geometries.forEach(g => g.dispose());
}
