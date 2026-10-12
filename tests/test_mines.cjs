const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create,payout}=require('../static/mines.js');

function element() {
  const classes=new Set(), listeners={};
  return {children:[],dataset:{},style:{},value:'',disabled:false,textContent:'',
    classList:{toggle(name,on){on?classes.add(name):classes.delete(name)},contains:name=>classes.has(name)},
    append(child){this.children.push(child)},get firstChild(){return this.children[0]},
    setAttribute(name,value){this[name]=value},getContext(){return {clearRect(){},fillRect(){}}},
    addEventListener(name,handler){listeners[name]=handler},
    click(){if(!this.disabled)listeners.click?.()},};
}
async function settle(){await new Promise(resolve=>setImmediate(resolve))}
function harness(fetch, preview=false) {
  const elements=new Map(), options=[3,5,7].map(count=>{const e=element();e.dataset.mineCount=count;return e});
  global.document={createElement:()=>element(),querySelectorAll:()=>options};
  let user={balance:50000,rounds_count:0},blocked=false;const history=[], effects=[];
  const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  const game=create({$,fetch,preview:()=>preview,user:()=>user,balance:()=>user.balance,
    account:value=>{user=value},busy:value=>{blocked=value},blocked:()=>blocked,
    requestId:()=> 'test-round',money:cents=>String(cents/100),history:round=>history.push(round),
    sound:{unlock(){},play:name=>effects.push(name)}});
  return {game,$,history,effects,get user(){return user}};
}
const active={id:'round-1',bet:1000,mine_count:3,status:'active',opened:[],safe_count:0,payout:0,win:0,next_multiplier:1.10};
const response=round=>({round,user:{balance:49000,rounds_count:0}});

test('a pending start locks the board and cannot send a duplicate stake',async()=>{
  let resolve,requests=0;
  const ui=harness(()=>{requests++;return new Promise(done=>resolve=done)});
  ui.$('mines-action').click();ui.$('mines-action').click();
  assert.equal(requests,1);assert.equal(ui.$('mines-action').disabled,true);
  assert.ok(ui.$('mines-board').children.every(e=>e.disabled));
  resolve(response(active));await settle();
  assert.equal(ui.user.balance,49000);assert.equal(ui.game.active,true);
  assert.equal(ui.$('mines-bet-plus').disabled,true);
  assert.equal(ui.$('mines-action').disabled,true);
  assert.ok(ui.$('mines-board').children.every(e=>!e.disabled));
});

test('returning to Mines restores opened cells and the cashout amount',async()=>{
  const ui=harness(async()=>response({...active,opened:[8,9],safe_count:2,payout:1246,next_multiplier:1.43}));
  await ui.game.enter();
  const cells=ui.$('mines-board').children;
  assert.equal(cells.length,25);assert.equal(cells[8].disabled,true);assert.equal(cells[9].disabled,true);
  assert.equal(cells[10].disabled,false);assert.equal(ui.$('mines-action').textContent,'Забрать 12.46 ₽');
  assert.equal(ui.$('mines-found').textContent,'2 / 22');
});

test('a lost cashout response is recovered from the settled server round',async()=>{
  let stage=0;const calls=[];
  const safe={...active,opened:[8],safe_count:1,payout:1100,next_multiplier:1.24};
  const cashed={...safe,status:'cashed',mines:[0,1,2],win:1100};
  const ui=harness(async(url)=>{
    calls.push(url);
    if(url.includes('cashout')){stage=1;throw new TypeError('response lost')}
    return stage?{round:cashed,user:{balance:50100,rounds_count:1}}:response(safe);
  });
  await ui.game.enter();ui.$('mines-action').click();await settle();
  assert.equal(ui.game.active,false);assert.equal(ui.user.balance,50100);
  assert.ok(calls.includes('/api/mines?round_id=round-1'));
  assert.ok(ui.$('mines-board').children.every(e=>e.disabled));
  assert.equal(ui.$('mines-action').disabled,false);
  assert.match(ui.$('mines-status').textContent,/уже на балансе/);
});

test('standalone preview plays a complete round and shares its balance/history',async()=>{
  const prior=Math.random;Math.random=()=>0;
  try {
    const ui=harness(()=>{throw new Error('preview must not call API')},true);
    ui.$('mines-action').click();await settle();
    assert.equal(ui.user.balance,49000);
    ui.$('mines-board').children[4].click();await settle();
    assert.equal(ui.$('mines-action').disabled,false);
    ui.$('mines-action').click();await settle();
    assert.equal(ui.user.balance,50100);assert.equal(ui.history.length,1);
    assert.equal(ui.history[0].game,'mines');assert.equal(ui.game.active,false);
    assert.equal(ui.user.rounds_count,1);
  }finally {Math.random=prior}
});

test('a mine loses the stake once and never returns a bonus',async()=>{
  let played=false;
  const lost={...active,status:'lost',opened:[0],safe_count:0,payout:0,win:0,mines:[0,1,2],next_multiplier:0};
  const ui=harness(async(url)=>{
    if(url.includes('/reveal'))played=true;
    return {round:played?lost:active,user:{balance:49000,rounds_count:played?1:0}};
  });
  await ui.game.enter();ui.$('mines-board').children[0].click();await settle();
  assert.equal(ui.user.balance,49000);assert.equal(ui.game.active,false);
  assert.equal(ui.$('mines-payout').textContent,'0 ₽');
  assert.equal(ui.$('mines-status').textContent,'Мина! Ставка 10 ₽ проиграна.');
  assert.ok(ui.$('mines-board').children.every(e=>e.disabled));
  await ui.game.enter();assert.equal(ui.user.balance,49000);
});

test('payouts use kopeks with a 96% factor and round down',()=>{
  assert.equal(payout(1000,3,0),0);assert.equal(payout(1000,3,1),1100);
  assert.equal(payout(1000,5,1),1200);assert.equal(payout(1000,7,1),1333);
  assert.equal(payout(1000,3,2),1246);assert.equal(payout(1000,5,2),1515);
});
