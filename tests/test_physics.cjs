const test = require('node:test');
const assert = require('node:assert/strict');
const {Simulation, createReplay, sample} = require('../static/physics.js');

test('gravity increases downward speed before the first contact', () => {
  const sim = new Simulation([{kind:'stone'}], 7);
  const initial = {...sim.body};
  for (let i = 0; i < 6; i++) sim.tick();
  assert.ok(sim.body.y > initial.y);
  assert.ok(sim.body.vy > initial.vy);
  assert.equal(sim.hits.length, 0);
});

test('a block contact produces a rebound, rotation and a real mined cell', () => {
  const sim = new Simulation([{kind:'diamond'}], 9);
  while (!sim.hits.length) sim.tick();
  const hit = sim.hits[0];
  assert.ok(sim.body.vy < 0);
  assert.ok(sim.body.omega !== 3.8);
  assert.equal(hit.row, 4);
  assert.ok(Math.abs(sim.body.y + sim.body.radius - hit.y) < .001);
  assert.ok(sim.cleared.has(`${hit.row}:${hit.col}`));
});

test('TNT kicks the pickaxe up more strongly than normal stone', () => {
  const stone = new Simulation([{kind:'stone'}], 61);
  const blast = new Simulation([{kind:'tnt'}], 61);
  while (!stone.hits.length) stone.tick();
  while (!blast.hits.length) blast.tick();
  assert.ok(blast.body.vy < stone.body.vy);
  assert.ok(Math.abs(blast.body.vx - stone.body.vx) > 1);
});

test('side walls contain the body and reverse its horizontal velocity', () => {
  const sim = new Simulation([{kind:'stone'}], 1);
  sim.body.x = 210; sim.body.vx = 150;
  sim.tick();
  assert.ok(sim.body.x <= 220 - sim.body.radius);
  assert.ok(sim.body.vx < 0);
});

test('replay is deterministic and can be sampled independently of rendering rate', () => {
  const steps = [{kind:'gold'}, {kind:'tnt'}, {kind:'emerald'}, {kind:'diamond'}];
  const a = createReplay(steps, 17), b = createReplay(steps, 17);
  assert.deepEqual(a.hits, b.hits);
  assert.deepEqual(sample(a, .7), sample(b, 42 / 60));
  assert.deepEqual(sample(a, a.duration + 100), sample(a, a.duration));
  assert.equal(a.hits.length, steps.length);
  for (const hit of a.hits) assert.equal(a.mined.get(`${hit.row}:${hit.col}`), hit.time);
});

test('different materials and blast trajectories complete without getting stuck on ledges', () => {
  const kinds = ['stone','copper','gold','emerald','diamond','tnt','heart','boost'];
  for (let seed = 0; seed < 256; seed++) {
    const steps = Array.from({length:48}, (_,i) => ({kind:kinds[(i + seed) % kinds.length]}));
    const replay = createReplay(steps, seed);
    assert.equal(replay.hits.length, 48, `seed ${seed}`);
    assert.ok(replay.duration < 60);
    assert.ok(replay.frames.every(f => Number.isFinite(f.x) && Number.isFinite(f.y) &&
      Number.isFinite(f.angle) && f.x >= 37 - .001 && f.x <= 203 + .001));
    assert.ok(replay.hits.every((hit,i) => i === 0 || hit.time > replay.hits[i-1].time));
  }
});

test('a high durability pickaxe can complete the full 96 block descent', () => {
  const kinds = ['diamond','tnt','heart','gold','stone','boost'];
  for (let seed = 0; seed < 32; seed++) {
    const replay = createReplay(Array.from({length:96}, (_, i) => ({kind:kinds[(i+seed)%kinds.length]})), seed);
    assert.equal(replay.hits.length, 96, `seed ${seed}`);
    assert.ok(replay.duration < 96);
    assert.ok(replay.frames.every(f => Number.isFinite(f.y) && f.x >= 37-.001 && f.x <= 203+.001));
  }
});
