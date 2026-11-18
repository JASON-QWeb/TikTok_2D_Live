export const vertexShader = /* glsl */ `
uniform vec4 pose;
uniform float neck;
varying vec2 photoUv;

void main() {
  photoUv = uv;
  vec3 point = position;
  vec3 pivot = vec3(0.0, neck, 0.03);
  vec3 head = point - pivot;
  float cy = cos(pose.x), sy = sin(pose.x);
  float cx = cos(pose.y), sx = sin(pose.y);
  float cz = cos(pose.z), sz = sin(pose.z);
  head.xz = vec2(cy * head.x + sy * head.z, cy * head.z - sy * head.x);
  head.yz = vec2(cx * head.y - sx * head.z, sx * head.y + cx * head.z);
  head.xy = vec2(cz * head.x - sz * head.y, sz * head.x + cz * head.y);
  float headWeight = smoothstep(neck - 0.11, neck + 0.11, point.y);
  point = mix(point, head + pivot, headWeight);
  float shoulderWeight = smoothstep(0.44, neck, position.y) * (1.0 - headWeight);
  point.y += pose.w * (0.0035 + 0.005 * shoulderWeight);
  point.x += pose.w * position.x * 0.008 * shoulderWeight;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(point, 1.0);
}
`;

export const fragmentShader = /* glsl */ `
uniform sampler2D photo;
uniform sampler2D atlas;
uniform vec2 photoSize;
uniform vec2 atlasSize;
uniform vec4 regions[6];
uniform vec3 strips[6];
uniform float frameCount;
uniform vec3 speech;
uniform float smile;
uniform vec2 lids;
uniform vec2 gaze;
varying vec2 photoUv;

vec3 readAtlas(vec2 point) {
  return texture2D(atlas, vec2(point.x / atlasSize.x, 1.0 - point.y / atlasSize.y)).rgb;
}

bool inRegion(vec2 p, vec4 region) {
  return all(greaterThanEqual(p, region.xy)) && all(lessThan(p, region.xy + region.zw));
}

vec3 frameAt(int index, vec2 p, float value) {
  vec3 strip = strips[index];
  float frame = clamp(value, 0.0, 1.0) * (frameCount - 1.0);
  vec2 start = strip.xy + p - regions[index].xy;
  vec2 stepX = vec2(strip.z, 0.0);
  vec3 previous = readAtlas(start + floor(frame) * stepX);
  vec3 next = readAtlas(start + min(floor(frame) + 1.0, frameCount - 1.0) * stepX);
  return mix(previous, next, fract(frame));
}

vec3 eyeAt(int index, vec2 p, float blink) {
  vec4 region = regions[index];
  vec2 local = (p - region.xy) / region.zw;
  float falloff = 1.0 - smoothstep(0.16, 0.47, length((local - 0.5) * vec2(1.0, 1.55)));
  vec2 shifted = p - gaze * falloff * (1.0 - blink);
  return frameAt(index, shifted, blink);
}

void main() {
  vec4 color = texture2D(photo, photoUv);
  if (color.a < 0.006) discard;
  vec2 p = vec2(photoUv.x, 1.0 - photoUv.y) * photoSize;
  if (inRegion(p, regions[0])) {
    if (smile > 0.005) color.rgb = mix(color.rgb, frameAt(5, p, smile), 1.0 - smoothstep(0.03, 0.24, speech.x));
    if (speech.x > 0.005) {
      vec3 vowel = frameAt(0, p, speech.x);
      if (speech.y > 0.01) vowel = mix(vowel, frameAt(3, p, speech.x), speech.y);
      if (speech.z > 0.01) vowel = mix(vowel, frameAt(4, p, speech.x), speech.z);
      color.rgb = mix(color.rgb, vowel, smoothstep(0.005, 0.09, speech.x));
    }
  }
  if (inRegion(p, regions[1])) color.rgb = eyeAt(1, p, lids.x);
  if (inRegion(p, regions[2])) color.rgb = eyeAt(2, p, lids.y);
  gl_FragColor = color;
  #include <colorspace_fragment>
}
`;
