/* Short reference-video effects, unlocked by the first interaction. */
(() => {
  'use strict';
  const names = ['start', 'hit', 'ore', 'blast', 'pickup', 'finish'];
  const buffers = new Map(), loading = new Map(), voices = new Set(), lastPlayed = new Map();
  let context, master, generation = 0, muted = false;
  try {muted = window.localStorage.getItem('pickaxe-muted') === '1'} catch (_) {}

  function load(name) {
    if (buffers.has(name)) return Promise.resolve(buffers.get(name));
    if (loading.has(name)) return loading.get(name);
    const promise = (async () => {
      const url = window.PickaxeAudioAssets?.[name];
      let bytes;
      if (url) {
        const binary = atob(url.split(',')[1]);
        bytes = Uint8Array.from(binary, c => c.charCodeAt(0)).buffer;
      } else {
        const response = await fetch(`/static/assets/sounds/${name}.wav`);
        if (!response.ok) throw new Error('Sound unavailable');
        bytes = await response.arrayBuffer();
      }
      const buffer = await context.decodeAudioData(bytes);
      buffers.set(name, buffer); return buffer;
    })().catch(() => null);
    loading.set(name, promise);
    promise.finally(() => loading.delete(name));
    return promise;
  }

  async function unlock() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio || muted) return false;
    try {
      if (!context) {
        context = new Audio(); master = context.createGain();
        master.gain.value = .42; master.connect(context.destination);
      }
      if (context.state === 'suspended') await context.resume();
      names.forEach(name => load(name));
      return context.state === 'running';
    } catch (_) {return false}
  }

  function stopAll() {
    generation++;
    for (const source of voices) {try {source.stop()} catch (_) {}}
    voices.clear(); lastPlayed.clear();
  }

  async function play(name, rate = 1) {
    if (muted || document.hidden || !names.includes(name)) return;
    const token = generation, started = Date.now();
    if (!await unlock() || token !== generation) return;
    const buffer = await load(name);
    // A sound from an old screen or a slow load must never play later.
    if (!buffer || muted || document.hidden || token !== generation || Date.now() - started > 250) return;
    const now = context.currentTime;
    if (now - (lastPlayed.get(name) ?? -Infinity) < .06 || voices.size >= 8) return;
    lastPlayed.set(name, now);
    const source = context.createBufferSource(); source.buffer = buffer;
    source.playbackRate.value = Math.max(.85, Math.min(1.2, rate));
    source.connect(master); source.onended = () => {voices.delete(source); source.disconnect()};
    voices.add(source); source.start();
  }

  function setMuted(value) {
    muted = Boolean(value);
    try {window.localStorage.setItem('pickaxe-muted', muted ? '1' : '0')} catch (_) {}
    if (muted) stopAll(); else unlock();
    return muted;
  }
  document.addEventListener('pointerdown', () => unlock(), {passive: true});
  document.addEventListener('keydown', () => unlock());
  document.addEventListener('visibilitychange', () => {if (document.hidden) stopAll()});
  window.PickaxeSound = {play, unlock, stopAll, setMuted, get muted() {return muted}};
})();
