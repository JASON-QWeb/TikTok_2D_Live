import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { mouthShape } from '../../frontend/src/portrait/articulation.js';

test('the default portrait ships its texture plates and facial relief', () => {
  const root = new URL('../../', import.meta.url);
  const config = JSON.parse(readFileSync(new URL('config/avatar3d.json', root)));
  const url = new URL(config.portrait.manifest.slice(1), root);
  const manifest = JSON.parse(readFileSync(url));
  const [columns, rows] = manifest.grid;
  assert.equal(manifest.depth.length, (columns + 1) * (rows + 1));
  assert.ok(Math.max(...manifest.depth) - Math.min(...manifest.depth) > 100);
  for (const name of ['mouth', 'round', 'wide', 'left', 'right', 'smile']) {
    const { start, stride, rect } = manifest.regions[name];
    assert.ok(start[0] + stride * (manifest.frames - 1) + rect[2] <= manifest.atlasSize[0]);
    assert.ok(start[1] + rect[3] <= manifest.atlasSize[1]);
  }
  for (const name of ['neutral.webp', 'expressions.webp']) {
    const file = readFileSync(new URL(name, url));
    assert.equal(file.toString('ascii', 8, 12), 'WEBP');
  }
});

test('audio of the same level distinguishes round and wide vowels, and silence closes the mouth', () => {
  const spectrum = new Float32Array(128).fill(-100);
  assert.equal(mouthShape(0.03, spectrum, 50).open, 0);
  spectrum[14] = -22;
  spectrum[26] = -24;
  const round = mouthShape(0.5, spectrum, 50);
  spectrum[26] = -100;
  spectrum[48] = -24;
  const wide = mouthShape(0.5, spectrum, 50);
  assert.ok(round.open > 0.4 && wide.open > 0.4);
  assert.ok(round.round > 0.7 && round.wide < 0.1);
  assert.ok(wide.wide > 0.7 && wide.round < 0.1);
});
