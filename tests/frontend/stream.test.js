import test from 'node:test';
import assert from 'node:assert/strict';
import { readEvents } from '../../frontend/src/transport.js';
import { ReplyCursor, SpeechPlayer } from '../../frontend/src/audio.js';
import { createPlaceholder, disposeObject } from '../../frontend/src/placeholder.js';
import { Avatar } from '../../frontend/src/avatar.js';
import { readFileSync } from 'node:fs';

const config = JSON.parse(readFileSync(new URL('../../config/avatar3d.json', import.meta.url)));

test('SSE handles split Chinese UTF-8 bytes, heartbeats and multiple frames', async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\nevent: state\r\ndata: {"reply":"你好"}\r\n\r\ndata: {"reply":"再见"}\n\n');
  const body = new ReadableStream({ start(controller) {
    for (let i = 0; i < bytes.length; i++) controller.enqueue(bytes.slice(i, i + 1));
    controller.close();
  } });
  const states = [];
  for await (const state of readEvents(body)) states.push(state);
  assert.deepEqual(states.map(s => s.reply), ['你好', '再见']);
});

test('cursor skips refresh history, accepts first live reply and never replays final markers', () => {
  const cursor = new ReplyCursor();
  assert.deepEqual(cursor.take([]), []);
  assert.equal(cursor.take([{ seq: 1, final: false }]).length, 1);
  assert.deepEqual(cursor.take([{ seq: 1, final: true }]), []);
  assert.deepEqual(cursor.take([{ seq: 3 }, { seq: 2 }]).map(r => r.seq), [2, 3]);
  cursor.reset();
  assert.deepEqual(cursor.take([{ seq: 1 }]), []);
  assert.equal(cursor.take([{ seq: 2 }])[0].seq, 2);
});

test('final metadata updates queued audio without replaying the same segment', () => {
  const player = new SpeechPlayer(() => { throw new Error('locked player must not fetch'); }, () => {}, () => {});
  player.ingest([]);
  player.ingest([{ seq: 1, reply: '第一段', final: false }]);
  player.ingest([{ seq: 1, reply: '第一段😄', final: true }]);
  assert.equal(player.queue.length, 1);
  assert.equal(player.queue[0].final, true);
  assert.equal(player.queue[0].reply, '第一段😄');
  player.dispose();
});

test('generated avatar has working mouth, blink, named rig and bounded head movement', () => {
  const root = createPlaceholder();
  const avatar = new Avatar(root, config);
  assert.deepEqual(avatar.capabilities, { mouth: true, blink: true, head: true });
  for (let i = 0; i < 1800; i++) avatar.update(1 / 60, 0.7, false);
  assert.ok(root.getObjectByName('Mouth').morphTargetInfluences[0] > 0.6);
  assert.ok(Math.abs(avatar.bones.head.node.rotation.y) < 0.15);
  for (let i = 0; i < 60; i++) avatar.update(1 / 60, 0, false);
  assert.ok(root.getObjectByName('Mouth').morphTargetInfluences[0] < 0.01);
  avatar.dispose();
});

test('static GLB-like geometry reports missing facial controls rather than claiming lip sync', () => {
  const root = createPlaceholder();
  root.traverse(node => { if (node.morphTargetDictionary) node.morphTargetDictionary = {}; });
  const avatar = new Avatar(root, config);
  assert.equal(avatar.capabilities.mouth, false);
  disposeObject(root);
});
