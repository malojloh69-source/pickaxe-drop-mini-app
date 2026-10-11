const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(require('node:path').join(__dirname,'../static/sound.js'), 'utf8');

function setup({deferDecode=false, storedMute='0'}={}) {
  const sources=[], contexts=[], decodeQueue=[], listeners={}, storage=new Map([['pickaxe-muted',storedMute]]);
  class AudioContext {
    constructor() {this.state='suspended';this.currentTime=1;contexts.push(this)}
    createGain() {return {gain:{value:0},connect(){}}}
    resume() {this.state='running';return Promise.resolve()}
    decodeAudioData(bytes) {
      return deferDecode ? new Promise(resolve=>decodeQueue.push(()=>resolve({bytes}))) : Promise.resolve({bytes});
    }
    createBufferSource() {
      const source={playbackRate:{value:1},connect(){},disconnect(){},
        start(){this.started=true},stop(){this.stopped=true;this.onended?.()}};
      sources.push(source);return source;
    }
  }
  const document={hidden:false,addEventListener:(n,fn)=>{(listeners[n]??=[]).push(fn)}};
  const env={document,AudioContext,console,atob,Date,
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    PickaxeAudioAssets:Object.fromEntries(['start','hit','ore','blast','pickup','finish'].map(n=>[n,'data:audio/wav;base64,UklGRg==']))};
  env.window=env;vm.runInNewContext(code,env);
  return {sound:env.PickaxeSound,sources,contexts,decodeQueue,document,storage,
    emit:n=>listeners[n]?.forEach(fn=>fn())};
}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};

test('audio stays silent until interaction, mute persists and stops active effects', async()=>{
  const e=setup();assert.equal(e.contexts.length,0);
  await e.sound.play('hit');assert.equal(e.sources.length,1);assert.equal(e.sources[0].started,true);
  e.sound.setMuted(true);assert.equal(e.sources[0].stopped,true);assert.equal(e.storage.get('pickaxe-muted'),'1');
  await e.sound.play('blast');assert.equal(e.sources.length,1);
  const restored=setup({storedMute:'1'});await restored.sound.play('hit');assert.equal(restored.contexts.length,0);
  restored.sound.setMuted(false);await restored.sound.play('ore');assert.equal(restored.sources.length,1);
});

test('leaving a screen cancels effects that are still loading', async()=>{
  const e=setup({deferDecode:true});const pending=e.sound.play('blast');await flush();
  assert.ok(e.decodeQueue.length>0);e.sound.stopAll();e.decodeQueue.forEach(resolve=>resolve());await pending;
  assert.equal(e.sources.length,0);
  await e.sound.play('blast');assert.equal(e.sources.length,1);
  e.document.hidden=true;e.emit('visibilitychange');assert.equal(e.sources[0].stopped,true);
  await e.sound.play('finish');assert.equal(e.sources.length,1);
});

test('rapid repeated contacts do not stack the same effect', async()=>{
  const e=setup();await e.sound.unlock();await flush();
  await Promise.all(Array.from({length:15},()=>e.sound.play('hit')));
  assert.equal(e.sources.length,1);
});
