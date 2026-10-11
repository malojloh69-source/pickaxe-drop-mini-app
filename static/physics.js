// The visual simulation uses logical pixels and a fixed 120 Hz clock.
// Round rewards and durability are settled separately by main.py.
(function (root, factory) {
  const physics = factory();
  if (typeof module === 'object' && module.exports) module.exports = physics;
  else root.PickaxePhysics = physics;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const DT = 1 / 120, TILE = 20, FIRST_ROW = 4;
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const materials = {stone: .34, copper: .28, gold: .37, emerald: .42,
    diamond: .48, heart: .22, boost: .46, tnt: .65};

  function random(seed) {
    let value = seed >>> 0;
    return () => {
      value += 0x6D2B79F5;
      let x = value;
      x = Math.imul(x ^ x >>> 15, x | 1);
      x ^= x + Math.imul(x ^ x >>> 7, x | 61);
      return ((x ^ x >>> 14) >>> 0) / 4294967296;
    };
  }

  class Simulation {
    constructor(steps, seed) {
      this.steps = steps;
      this.random = random(seed);
      this.time = 0;
      this.index = 0;
      this.hits = [];
      this.impacts = [];
      this.cleared = new Set();
      this.mined = new Map();
      this.body = {x: 120, y: 38, vx: (this.random() < .5 ? -1 : 1) * 32,
        vy: 8, angle: -.15, omega: 3.8, radius: 17, focusY: 38};
    }

    // Resolve a round collider against the sides of previously mined cells.
    // This keeps a sideways rebound inside the actual shaft, rather than
    // allowing the pickaxe to pass through the stone surrounding it.
    resolveShaft() {
      const b = this.body;
      for (let pass = 0; pass < 2; pass++) {
        const first = Math.max(FIRST_ROW, Math.floor((b.y - b.radius) / TILE));
        const last = Math.min(FIRST_ROW + this.index - 1, Math.floor((b.y + b.radius) / TILE));
        const left = clamp(Math.floor((b.x - b.radius) / TILE), 1, 10);
        const right = clamp(Math.floor((b.x + b.radius) / TILE), 1, 10);
        for (let row = first; row <= last; row++) for (let col = left; col <= right; col++) {
          if (this.cleared.has(`${row}:${col}`)) continue;
          const closestX = clamp(b.x, col * TILE, (col + 1) * TILE);
          const closestY = clamp(b.y, row * TILE, (row + 1) * TILE);
          const dx = b.x - closestX, dy = b.y - closestY;
          const distance = Math.hypot(dx, dy);
          if (distance >= b.radius || distance < 1e-7) continue;
          const nx = dx / distance, ny = dy / distance;
          const approach = b.vx * nx + b.vy * ny;
          // A blast can throw the pickaxe onto an unmined ledge. Break that
          // stone on contact so a valid round cannot settle on it forever.
          if (ny < -.5 && approach < 0) {
            this.cleared.add(`${row}:${col}`);
            this.mined.set(`${row}:${col}`, this.time);
            this.impacts.push({index: null, time: this.time, row, col,
              x: b.x, y: row * TILE, speed: -approach, kind: 'stone'});
            b.vy = -Math.min(24, -approach * .2);
            continue;
          }
          const overlap = b.radius - distance + .001;
          b.x += nx * overlap; b.y += ny * overlap;
          if (approach < 0) {
            b.vx -= 1.55 * approach * nx;
            b.vy -= 1.55 * approach * ny;
            b.omega = clamp(b.omega - nx * approach * .035, -9, 9);
          }
        }
      }
    }

    tick() {
      const b = this.body;
      this.time += DT;
      b.vy = Math.min(190, b.vy + 320 * DT);
      b.vx *= Math.exp(-.12 * DT);
      b.omega *= Math.exp(-.1 * DT);
      b.x += b.vx * DT; b.y += b.vy * DT; b.angle += b.omega * DT;

      const minX = TILE + b.radius, maxX = 11 * TILE - b.radius;
      if (b.x < minX || b.x > maxX) {
        b.x = clamp(b.x, minX, maxX);
        b.vx *= -.62;
        b.omega = -b.omega * .65;
      }
      this.resolveShaft();

      if (this.index < this.steps.length) {
        const row = FIRST_ROW + this.index, floor = row * TILE;
        if (b.vy > 0 && b.y + b.radius >= floor) {
          const step = this.steps[this.index];
          const speed = b.vy;
          b.y = floor - b.radius;
          const col = clamp(Math.floor(b.x / TILE), 1, 10);
          // Clear every cell touched by the collider, not an unrelated tile.
          const columns = [];
          for (let c = clamp(Math.floor((b.x - b.radius - 1) / TILE), 1, 10);
            c <= clamp(Math.floor((b.x + b.radius + 1) / TILE), 1, 10); c++) {
            this.cleared.add(`${row}:${c}`);
            this.mined.set(`${row}:${c}`, this.time); columns.push(c);
          }
          const hit = {index: this.index, time: this.time, row, col, columns,
            x: b.x, y: floor, speed, kind: step.kind};
          this.hits.push(hit); this.impacts.push(hit); this.index++;

          b.vy = -(step.kind === 'tnt' ? 92 : Math.min(52, speed * (materials[step.kind] || .34)));
          b.vx = clamp(b.vx * .8 + (this.random() - .5) * (step.kind === 'tnt' ? 100 : 44), -72, 72);
          const contactOffset = b.x - (col * TILE + TILE / 2);
          b.omega = clamp(b.omega * .96 + contactOffset * .12 + (this.random() - .5) * 2.2, -9, 9);
        }
      }
      b.focusY += (b.y - b.focusY) * (1 - Math.exp(-12 * DT));
      return {x: b.x, y: b.y, vx: b.vx, vy: b.vy, angle: b.angle,
        omega: b.omega, focusY: b.focusY};
    }
  }

  function createReplay(steps, seed) {
    if (!Array.isArray(steps) || !steps.length || steps.length > 96) throw new Error('Invalid round');
    const simulation = new Simulation(steps, seed);
    const frames = [{...simulation.body}];
    let ending = Infinity;
    for (let i = 0; i < 120 * 96; i++) {
      frames.push(simulation.tick());
      if (simulation.index === steps.length && ending === Infinity) ending = simulation.time + .32;
      if (simulation.time >= ending) break;
    }
    if (simulation.index !== steps.length) throw new Error('Pickaxe simulation did not finish');
    const byRow = new Map();
    for (const hit of simulation.hits) {
      byRow.set(hit.row, hit);
    }
    return {frames, hits: simulation.hits, duration: (frames.length - 1) * DT,
      dt: DT, byRow, mined: simulation.mined, impacts: simulation.impacts};
  }

  function sample(replay, time) {
    const position = clamp(time / replay.dt, 0, replay.frames.length - 1);
    const index = Math.floor(position), fraction = position - index;
    const a = replay.frames[index], b = replay.frames[Math.min(index + 1, replay.frames.length - 1)];
    const body = {};
    for (const key of ['x','y','vx','vy','angle','omega','focusY']) body[key] = a[key] + (b[key] - a[key]) * fraction;
    return body;
  }

  return {Simulation, createReplay, sample, DT, TILE, FIRST_ROW};
});
