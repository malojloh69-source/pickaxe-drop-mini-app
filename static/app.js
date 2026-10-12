(() => {
  'use strict';

  const tg = window.Telegram?.WebApp;
  const physics = window.PickaxePhysics;
  const sound = window.PickaxeSound;
  const $ = id => document.getElementById(id);
  let pickaxes = [
    {id:'stone', name:'Каменная', max_hp:100, chance:50},
    {id:'iron', name:'Железная', max_hp:150, chance:35},
    {id:'diamond', name:'Алмазная', max_hp:250, chance:15},
  ];
  const state = {user: null, view: 'home', fast: false,
    running: false, round: null, step: -1, displayedWin: 0, balance: 0, lastWin: 0, frame: 0,
    preview: false, previewHistory: [], progress: 0, requestPending: false,
    elapsed: 0, lastTick: 0, time: 0, particles: [], shakeUntil: 0, resultAccounted: false,
    replay: null, body: null, simulationTime: 0, impact: -1,
    pickaxe: 'diamond', visualPick: 'diamond', maxHp: 250, rollIndex: -1, reel: null,
    revealPlayed: false, sceneSize: '', controlHeight: 210};
  const rollDuration = 2400, spinStart = 220, spinEnd = 1850, zoomOutStart = 2110;
  const launchDuration = 700, flightStart = rollDuration + launchDuration, reelPitch = 58;
  const clamp = (n, min=0, max=1) => Math.max(min,Math.min(max,n));
  const smooth = p => p*p*(3-2*p);
  const world = $('game-world');
  const ctx = world.getContext('2d', {alpha: false});
  ctx.imageSmoothingEnabled = false;

  const money = cents => new Intl.NumberFormat('ru-RU', {minimumFractionDigits: cents % 100 ? 2 : 0,
    maximumFractionDigits: 2}).format(cents / 100);
  const requestId = () => window.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16);
  });
  const jsonFetch = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: {'X-Telegram-Init-Data': tg?.initData || '',
      ...(options.body ? {'Content-Type': 'application/json'} : {})}});
    if (!response.headers.get('Content-Type')?.includes('application/json')) {
      const error = new Error('API игры недоступен'); error.status = 0; throw error;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `Ошибка ${response.status}`);
      error.status = response.status; throw error;
    }
    return data;
  };

  function avatar(element, user) {
    element.replaceChildren();
    if (user.photo_url) {
      const img = document.createElement('img'); img.src = user.photo_url; img.alt = '';
      img.onerror = () => {element.textContent = user.name.charAt(0).toUpperCase()};
      element.append(img);
    } else element.textContent = user.name.charAt(0).toUpperCase();
  }
  function updateUser() {
    if (!state.user) return;
    for (const id of ['home-name', 'profile-name', 'game-name']) $(id).textContent = state.user.name;
    $('profile-username').textContent = state.user.username ? `@${state.user.username}` : 'Telegram';
    for (const id of ['home-avatar', 'profile-avatar', 'game-avatar']) avatar($(id), state.user);
    $('home-balance').textContent = `${money(state.balance)} ₽`;
    $('profile-balance').textContent = `${money(state.balance)} ₽`;
    setAmount('game-balance', state.balance);
    $('mines-balance').textContent = `${money(state.balance)} ₽`;
    const rounds = state.user.rounds_count || 0, level = 1 + Math.floor(rounds / 10);
    $('level-current').textContent = level; $('level-next').textContent = level + 1;
    $('level-fill').style.width = `${rounds % 10 * 10}%`;
    account.sync();
  }
  function setAmount(id, cents) {
    const element=$(id), text=money(cents);
    element.textContent=text;
    // Reduce long amounts within their own column, preserving the full value.
    const available=element.clientWidth || (Math.min(540,window.innerWidth||390)-74)/2;
    ctx.save();ctx.font='750 28px Inter';const measured=ctx.measureText(text).width;ctx.restore();
    element.style.setProperty('--amount-size', `${Math.max(12,Math.min(28,28*(available-2)/Math.max(1,measured)))}px`);
  }
  function updateBet() {
    const busy = !state.user || state.running || state.requestPending;
    const valid = pickaxeBet.render(busy, state.balance);
    $('spin-button').disabled = busy || !valid;
    $('repeat-button').disabled = busy || !valid;
    mines.render();
  }
  let viewTimer;
  function clearViewTransition() {
    clearTimeout(viewTimer);
    for (const name of ['home', 'profile', 'game', 'mines']) {
      const element = $(`${name}-view`);
      element.classList.remove('view-entering', 'view-leaving');
      element.style.removeProperty('top');
      element.style.removeProperty('--view-offset');
      element.classList.toggle('hidden', name !== state.view);
      element.inert = name !== state.view || account.active;
      element.setAttribute('aria-hidden', String(name !== state.view));
    }
  }
  async function showView(view) {
    if (!['home', 'profile', 'game', 'mines'].includes(view) || state.view === view) return;
    clearViewTransition();
    const previous = state.view, oldScroll = window.scrollY || 0;
    const outgoing = $(`${previous}-view`), incoming = $(`${view}-view`);
    if (state.running && view !== 'game') finishRound();
    sound?.stopAll(); sound?.play('start', 1.12);
    state.view = view;
    window.scrollTo(0, 0);
    clearViewTransition();
    if (!document.hidden && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      outgoing.classList.remove('hidden');
      outgoing.style.top = `${-oldScroll}px`;
      outgoing.classList.add('view-leaving');
      incoming.style.setProperty('--view-offset', view === 'home' ? '-16px' : '16px');
      incoming.classList.add('view-entering');
      viewTimer = setTimeout(clearViewTransition, 290);
    }
    if (tg?.BackButton) {
      if (view === 'home') tg.BackButton.hide(); else tg.BackButton.show();
    }
    if (view === 'game') {sizeWorld();updateUser();setAmount('game-win',state.running?state.displayedWin:state.lastWin);drawWorld(state.progress,state.time);startRenderer()}
    else stopRenderer();
    if (view === 'mines') await mines.enter();
    if (view === 'profile') await loadHistory();
    if (view === 'profile') await refreshUser();
    if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
  }
  async function loadHistory() {
    const list = $('history-list');
    try {
      const {rounds} = state.preview ? {rounds: state.previewHistory} : await jsonFetch('/api/history');
      list.replaceChildren();
      if (!rounds.length) {list.innerHTML = '<p class="empty-history">Сыграй первый раунд, и он появится здесь.</p>'; return}
      for (const round of rounds) {
        const row = document.createElement('div'); row.className = 'history-item';
        const left = document.createElement('span');
        const title = document.createElement('strong'); title.textContent = round.game === 'mines' ? 'Мины' : 'Pickaxe Drop';
        const detail = document.createElement('small');
        detail.textContent = `${new Date(round.created_at * 1000).toLocaleDateString('ru-RU')} · ставка ${money(round.cost)} ₽`;
        left.append(title, detail);
        const amount = document.createElement('b'); amount.textContent = `+${money(round.win)} ₽`;
        row.append(left, amount); list.append(row);
      }
    } catch (err) {list.textContent = err.message}
  }
  async function refreshUser() {
    if(!state.user||state.preview||state.running||state.requestPending)return;
    try {
      const data=await jsonFetch('/api/me');
      if(state.running||state.requestPending)return;
      state.user=data.user;state.balance=data.user.balance;updateUser();account.configure(data);
    }catch (_) {}
  }
  let toastTimer;
  function toast(message) {
    const box = $('game-toast'); box.textContent = message; box.classList.remove('hidden');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => box.classList.add('hidden'), 1400);
  }
  function openHelp() {
    sound?.play('start', 1.12);
    $('help-modal').classList.remove('hidden');$('help-close').focus();tg?.BackButton?.show();
  }
  function closeHelp() {
    $('help-modal').classList.add('hidden');
    if(state.view==='home')tg?.BackButton?.hide();
  }
  function previewRound(bet) {
    // Local visual preview only. Public Telegram play always uses /api/play.
    let draw = Math.random() * pickaxes.reduce((sum, p) => sum + p.chance, 0);
    const pickaxe = pickaxes.find(p => (draw -= p.chance) < 0) || pickaxes[0];
    const max_hp = pickaxe.max_hp;
    let health = max_hp, position = 5, multiplier = 1, win = 0;
    const cost=Math.round(bet*100), maxWin=Math.min(cost*2,Number.MAX_SAFE_INTEGER-state.balance+cost);
    const reservedBonus=Math.max(1,Math.floor(cost*5/100)),oreLimit=maxWin-reservedBonus;
    if(Math.floor(cost*110/100)>maxWin)throw new Error('Сумма слишком велика для баланса');
    const kinds = ['stone','copper','gold','emerald','diamond','tnt','heart','boost'];
    const weights = [28,20,16,9,4,8,8,7];
    const rewards = {copper:.015,gold:.04,emerald:.08,diamond:.25};
    const steps = [];
    for (let depth = 1; depth <= 96; depth++) {
      position = Math.max(1, Math.min(10, position + [-1,0,0,1][Math.random()*4|0]));
      let choice = Math.random()*100, kind = 'stone';
      for (let i=0;i<kinds.length;i++) {choice-=weights[i];if(choice<0){kind=kinds[i];break}}
      let gain=0;
      if(kind==='tnt')health=Math.max(0,health-(16+Math.random()*9|0));
      else if(kind==='heart')health=Math.min(max_hp,health+(9+Math.random()*7|0));
      else health=Math.max(0,health-(2+Math.random()*4|0));
      if(rewards[kind]) {gain=Math.min(oreLimit-win,Math.round(cost*rewards[kind]*multiplier));win+=gain}
      if(kind==='boost')multiplier=Math.min(2,multiplier+1);
      steps.push({depth,x:position,kind,hp:health,gain,multiplier});
      if(health===0)break;
    }
    let mine=steps.find(step=>step.kind==='tnt');
    if(!mine){mine=steps[steps.length-1];mine.kind='tnt';const previous=steps.length>1?steps[steps.length-2].hp:max_hp;
      health=Math.max(0,previous-(16+Math.random()*9|0));mine.hp=health}
    const mine_bonus=reservedBonus;mine.gain+=mine_bonus;win+=mine_bonus;
    const guaranteed_win=Math.min(maxWin,Math.floor(cost*110/100));const guarantee=Math.max(0,guaranteed_win-win);
    steps[steps.length-1].gain+=guarantee;win+=guarantee;
    const start_balance=state.balance;
    const result={id:requestId(),bet:bet*100,cost,pickaxe,max_hp,steps,win,health,physics_seed:Math.random()*4294967296>>>0,
      depth:steps.length,multiplier,mine_bonus,guaranteed_win,start_balance,balance:start_balance-cost+win};
    state.previewHistory.unshift({id:result.id,bet:result.bet,cost,win,created_at:Date.now()/1000|0});
    return result;
  }
  function clearDrop() {
    $('drop-caption').classList.add('hidden');
    $('active-pick').classList.add('hidden');
  }
  function makeReel(seed) {
    const random = rand(seed ^ 0x7069636b), reel = [state.visualPick];
    for (let i=1;i<=18;i++) {
      let n = random() * pickaxes.reduce((sum,p)=>sum+p.chance,0);
      reel.push((pickaxes.find(p=>(n-=p.chance)<0)||pickaxes[0]).id);
    }
    reel[18] = state.pickaxe;
    // One extra slot gives the clipped well a complete edge during deceleration.
    reel.push(pickaxes[0].id);
    return reel;
  }
  function rollPosition() {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return 18;
    const p = clamp((state.elapsed-spinStart)/(spinEnd-spinStart));
    return 18*(1-Math.pow(1-p,3));
  }
  function rollPick() {
    const index = Math.round(rollPosition());
    if (index !== state.rollIndex) {
      state.rollIndex = index; state.visualPick = state.reel[index];
      if (index > 0 && index < 18) sound?.play('hit', 1.2);
    }
    if (rollPosition() === 18 && !state.revealPlayed) {
      state.revealPlayed = true; state.visualPick = state.pickaxe;
      const pick = pickaxes.find(p => p.id === state.pickaxe);
      $('drop-caption').textContent = `${pick.name} кирка · ${state.maxHp} HP`;
      sound?.play('pickup'); tg?.HapticFeedback?.selectionChanged();
    }
  }
  async function play() {
    if (state.running || state.requestPending || !state.user) return;
    if (mines.active) {toast('Сначала завершите раунд «Мины»'); return}
    const bet = pickaxeBet.value, cost = pickaxeBet.cost;
    if (cost === null) {toast('Введи ставку от 1 ₽'); return}
    if (state.balance < cost) {toast('НЕ ХВАТАЕТ ₽'); return}
    sound?.unlock(); sound?.play('start');
    state.running = true; state.requestPending = true; state.round = null;
    state.step = -1; state.progress = 0; state.elapsed = 0; state.particles = [];
    state.replay = null; state.body = null; state.simulationTime = 0; state.impact = -1;
    state.rollIndex = -1; state.reel = null; state.revealPlayed = false; clearDrop();
    $('drop-caption').textContent = 'ВЫБИРАЕМ КИРКУ…'; $('drop-caption').classList.remove('hidden');
    state.resultAccounted = false; updateBet();
    $('result-panel').classList.add('hidden'); $('idle-caption').classList.add('hidden');
    $('game-health').classList.remove('hidden'); $('health-value').textContent = 'Выбираем кирку';
    $('spin-button').classList.add('running'); $('game-win').textContent = '0';
    drawWorld(0);
    try {
      const round = state.preview ? previewRound(bet) : await jsonFetch('/api/play',
        {method: 'POST', body: JSON.stringify({bet,request_id: requestId()})});
      state.requestPending = false; state.round = round; state.step = -1; state.displayedWin = 0;
      const pick = round.pickaxe || pickaxes[1];
      state.pickaxe = pick.id; state.maxHp = round.max_hp || pick.max_hp;
      $('active-pick-name').textContent = `${pick.name} · ${state.maxHp} HP`;
      const activeCanvas = $('active-pick-icon');
      activeCanvas.getContext('2d').clearRect(0,0,32,32);
      pixelPick(activeCanvas.getContext('2d'),16,15,1,0,state.pickaxe);
      $('health-value').textContent = `${state.maxHp}/${state.maxHp}`;
      if (!state.running || state.view !== 'game') {
        state.running = false; presentResult(); updateBet(); return;
      }
      const seed = round.physics_seed ?? [...round.id].reduce((n, c) => Math.imul(n ^ c.charCodeAt(0), 16777619) >>> 0, 2166136261);
      state.replay = physics.createReplay(round.steps, seed);
      state.reel = makeReel(seed);
      state.body = physics.sample(state.replay, 0);
      state.balance = round.start_balance - round.cost; updateUser();
      state.lastTick = 0; startRenderer();
    } catch (err) {
      state.requestPending = false; state.running = false; $('spin-button').classList.remove('running');
      if (state.round) {presentResult(); updateBet(); toast('Раунд завершён'); return}
      $('game-health').classList.add('hidden'); $('idle-caption').classList.remove('hidden');
      clearDrop();
      updateBet(); toast(err.message);
    }
  }
  function finishRound() {
    if (!state.running) return;
    state.running = false;
    $('spin-button').classList.remove('running');
    if (state.round) presentResult();
    if (state.round && state.view === 'game') sound?.play('finish');
    updateBet();
  }
  function presentResult() {
    const round = state.round;
    state.balance = round.balance; state.user.balance = state.balance;
    if (!state.resultAccounted) {
      state.user.rounds_count = (state.user.rounds_count || 0) + 1;
      state.resultAccounted = true;
    }
    state.lastWin = round.win; state.progress = round.steps.length;
    setAmount('game-win', round.win);
    $('result-win').textContent = `${money(round.win)} ₽`;
    $('result-depth').textContent = `Глубина: ${round.depth} блоков · прочность ${round.health}/${state.maxHp}`;
    $('health-value').textContent = `${round.health}/${state.maxHp}`;
    state.visualPick = state.pickaxe; clearDrop();
    $('result-panel').classList.remove('hidden');
    $('game-toast').classList.add('hidden');
    drawWorld(state.progress); updateUser();
  }

  function startRenderer() {
    if (!state.frame && !document.hidden && state.view === 'game') {
      state.lastTick = 0; state.frame = requestAnimationFrame(animate);
    }
  }
  function stopRenderer() {
    cancelAnimationFrame(state.frame); state.frame = 0; state.lastTick = 0;
  }
  function animate(now) {
    state.frame = 0;
    if (document.hidden || state.view !== 'game') return;
    sizeWorld();
    const dt = Math.min(64, Math.max(0, now - (state.lastTick || now)));
    state.lastTick = now; state.time = now / 1000;
    if (state.running && state.round && state.replay && !state.requestPending) {
      const round = state.round;
      state.elapsed += dt * (state.fast ? 2 : 1);
      state.simulationTime = Math.min(state.replay.duration, Math.max(0, (state.elapsed - flightStart) / 1000));
      state.body = physics.sample(state.replay, state.simulationTime);
      if (state.elapsed < rollDuration) rollPick();
      else if (!$('drop-caption').classList.contains('hidden')) {
        state.visualPick = state.pickaxe; $('drop-caption').classList.add('hidden');
        $('active-pick').classList.remove('hidden');
      }
      while (state.step + 1 < state.replay.hits.length && state.replay.hits[state.step + 1].time <= state.simulationTime) {
        const event = round.steps[++state.step];
        state.progress = state.step + 1;
        state.displayedWin += event.gain;
        setAmount('game-win', state.displayedWin);
        $('health-value').textContent = `${event.hp}/${state.maxHp}`;
        if (event.kind === 'diamond') toast(`АЛМАЗ · +${money(event.gain)} ₽`);
        if (event.kind === 'boost') toast(`ДОБЫЧА ×${event.multiplier}`);
        if (event.kind === 'tnt') {toast(`МИНА · +${money(event.gain)} ₽`);tg?.HapticFeedback?.impactOccurred('light')}
      }
      while (state.impact + 1 < state.replay.impacts.length && state.replay.impacts[state.impact + 1].time <= state.simulationTime) {
        spawnImpact(state.replay.impacts[++state.impact]);
      }
      const seconds = dt / 1000 * (state.fast ? 2 : 1);
      for (const p of state.particles) {
        p.life -= seconds; p.x += p.vx * seconds;
        p.y += p.vy * seconds; p.vy += 220 * seconds;
      }
      state.particles = state.particles.filter(p => p.life > 0);
      if (state.elapsed < flightStart) drawIdle(state.time);
      else drawWorld(state.progress, state.time);
      if (state.elapsed >= flightStart + state.replay.duration * 1000) finishRound();
    } else drawIdle(state.time);
    state.frame = requestAnimationFrame(animate);
  }
  function spawnImpact(event) {
    sound?.play(event.kind === 'tnt' ? 'blast' : ['heart','boost'].includes(event.kind) ? 'pickup' :
      ['gold','copper','emerald','diamond'].includes(event.kind) ? 'ore' : 'hit');
    const colors = {gold:['#ffdc5d','#fff4b1'],copper:['#d49a54','#efc07c'],
      emerald:['#44dd74','#a1ffaa'],diamond:['#55e7d9','#c1fff0'],
      tnt:['#ff5138','#ffbf49'],heart:['#ff728f','#ffa6b3'],boost:['#b6f226','#e8ffa0']};
    const color = colors[event.kind] || ['#778b8d','#c0cecb'];
    for (let i=0;i<(event.kind==='tnt'?20:9);i++) {
      const speed = event.kind === 'tnt' ? 60 : 35;
      state.particles.push({x:event.x,y:event.y,
        vx:(Math.random()-.5)*speed,vy:-12-Math.random()*40,
        life:.3+Math.random()*.35,color:color[i%2],size:i%4===0?2:1});
    }
    if (event.kind==='tnt') state.shakeUntil = state.time + .2;
  }

  // All game sprites use one pixel grid and transparent edges.
  function rand(seed) {
    let x = seed >>> 0;
    return () => {x += 0x6D2B79F5; let t = x; t = Math.imul(t ^ t >>> 15, t | 1);
      t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296};
  }
  const handlePalette = {W:'#412b18',A:'#815322',G:'#bc883d',L:'#f6c77a'};
  const palettes = {
    stone: {K:'#3e4c53',D:'#697a82',T:'#9eafb0',B:'#c5d0cd',H:'#edf2e8'},
    iron: {K:'#405961',D:'#88a3ad',T:'#c6d9df',B:'#eff9f8',H:'#ffffff'},
    diamond: {K:'#18595b',D:'#158c8b',T:'#2fd1c4',B:'#82f4df',H:'#d1ffee'},
  };
  const pickPixels = [
    '................................',
    '................................',
    '.........KKKKKKKKKK.............',
    '......KKKHHHHHHHHBBKK...........',
    '....KKHHBBBBBBBBBBBTBK..........',
    '...KHBBBBBBBBBBBBTTTTBK.........',
    '...KBBTTTTTTTTTTTTTTTTBK........',
    '...KDTTTTTTTTTTTTTTTTTTBK.......',
    '....KKDDDDDDKKKTTTTTTTTBK.......',
    '......KKKKKK...KKTTTTTTTBK......',
    '.................KTTTTTTBK......',
    '................WAKTTTTTBK......',
    '...............WLGAWKTTTBK......',
    '..............WLGAW.KTTTBK......',
    '.............WLGAW..KTTTBK......',
    '............WLGAW...KTTTBK......',
    '...........WLGAW....KTTTBK......',
    '..........WLGAW.....KTTTBK......',
    '.........WLGAW......KTTTBK......',
    '........WLGAW.......KTTDBK......',
    '.......WLGAW........KTDHK.......',
    '......WLGAW.........KDDK........',
    '.....WLGAW..........KKK.........',
    '....WLGAW.......................',
    '...WLGAW........................',
    '..WLGAW.........................',
    '..WGAW..........................',
    '..WWW...........................',
    '................................',
  ];
  const pickSprites = new Map();
  for (const [id, colors] of Object.entries(palettes)) {
    const sprite = document.createElement('canvas'); sprite.width = sprite.height = 32;
    const pickCtx = sprite.getContext('2d'), palette = {...handlePalette, ...colors};
    pickPixels.forEach((line,y) => [...line].forEach((pixel,x) => {
      if (palette[pixel]) {pickCtx.fillStyle=palette[pixel];pickCtx.fillRect(x,y,1,1)}
    }));
    pickSprites.set(id, sprite);
  }
  function pixelPick(c,x,y,scale=1,angle=0,type='diamond') {
    c.save();c.translate(Math.round(x),Math.round(y));c.rotate(angle);
    c.imageSmoothingEnabled=false;
    c.drawImage(pickSprites.get(type) || pickSprites.get('iron'),-16*scale,-14*scale,32*scale,32*scale);c.restore();
  }
  const glyphs = {
    '0':['01110','10001','10011','10101','11001','10001','01110'],
    '1':['00100','01100','00100','00100','00100','00100','01110'],
    '2':['01110','10001','00001','00010','00100','01000','11111'],
    '3':['11110','00001','00001','01110','00001','00001','11110'],
    '4':['00010','00110','01010','10010','11111','00010','00010'],
    '5':['11111','10000','10000','11110','00001','00001','11110'],
    '6':['01110','10000','10000','11110','10001','10001','01110'],
    '7':['11111','00001','00010','00100','01000','01000','01000'],
    '8':['01110','10001','10001','01110','10001','10001','01110'],
    '9':['01110','10001','10001','01111','00001','00001','01110'],
    '/':['00001','00001','00010','00100','01000','10000','10000'],
    'P':['11110','10001','10001','11110','10000','10000','10000'],
    'I':['111','010','010','010','010','010','111'],
    'C':['01111','10000','10000','10000','10000','10000','01111'],
    'K':['10001','10010','10100','11000','10100','10010','10001'],
    'A':['01110','10001','10001','11111','10001','10001','10001'],
    'X':['10001','10001','01010','00100','01010','10001','10001'],
    'E':['11111','10000','10000','11110','10000','10000','11111'],
    'D':['11110','10001','10001','10001','10001','10001','11110'],
    'R':['11110','10001','10001','11110','10100','10010','10001'],
    'O':['01110','10001','10001','10001','10001','10001','01110'],
  };
  function pixelText(c,text,x,y,scale=1,color='#fff',outline='#111c21') {
    const pixels=[];let offset=0;
    for (const char of text) {
      const glyph=glyphs[char];if(!glyph){offset+=4*scale;continue}
      glyph.forEach((line,dy)=>[...line].forEach((bit,dx)=>{if(bit==='1')pixels.push([x+offset+dx*scale,y+dy*scale])}));
      offset+=(glyph[0].length+1)*scale;
    }
    if(outline){c.fillStyle=outline;for(const [px,py] of pixels)c.fillRect(px-1,py-1,scale+2,scale+2)}
    c.fillStyle=color;for(const [px,py] of pixels)c.fillRect(px,py,scale,scale);
    return offset;
  }
  function drawMark() {
    const c=$('game-mark').getContext('2d');c.clearRect(0,0,96,40);c.imageSmoothingEnabled=false;
    pixelPick(c,10,9,.65,-.12);pixelText(c,'PICKAXE',22,4,1,'#f9ffef','#142422');
    pixelText(c,'DROP',3,16,3,'#f1d23e','#15211d');
    c.fillStyle='#fff4a5';c.fillRect(3,16,9,1);c.fillRect(21,16,9,1);c.fillRect(39,16,9,1);
  }
  const tiles = new Map();
  function tile(type,variant=0) {
    const key=`${type}:${variant}`;if(tiles.has(key))return tiles.get(key);
    const canvas=document.createElement('canvas');canvas.width=canvas.height=20;
    const c=canvas.getContext('2d');const r=rand((variant+1)*106733+type.length*271);
    const p=(x,y,w,h,color)=>{c.fillStyle=color;c.fillRect(x,y,w,h)};
    if(type==='sky') {
      p(0,0,20,20,'#99bde8');
      p((variant*7+3)%17,5,3,1,'#aac9ee');p((variant*3+9)%17,16,2,1,'#9fc3eb');
    } else if(type==='dirt'||type==='grass') {
      p(0,0,20,20,'#825934');
      for(let i=0;i<20;i++)p(r()*18|0,r()*19|0,2+(i%3===0?1:0),1,i%2?'#6c472c':'#a47743');
      if(type==='grass') {
        p(0,0,20,3,'#81c950');p(0,3,20,2,'#4c9239');
        for(let i=0;i<5;i++)p(i*4,4,2,2+i%2,'#4c9239');
      }
    } else if(type==='cobble') {
      p(0,0,20,20,'#525e65');
      for(const [x,y,w,h] of [[-2,-1,10,8],[9,-2,10,9],[17,6,7,9],[-2,9,8,10],[7,9,10,9],[4,18,9,7],[15,17,10,8]]) {
        p(x+2,y,w-4,h,'#879396');p(x+1,y+1,w-2,h-2,'#879396');p(x,y+3,w,h-5,'#879396');
        p(x+3,y+1,w-5,1,'#acb7b4');p(x+1,y+3,1,h-6,'#a0aeac');
        p(x+2,y+h-2,w-4,1,'#6b797e');p(x+w-2,y+3,1,h-6,'#6b797e');
      }
    } else {
      p(0,0,20,20,'#929fa1');
      for(let i=0;i<20;i++) {
        const x=r()*18|0,y=r()*19|0;
        p(x,y,2+(i%4===0?2:0),1,i%3?'#a4afad':'#7c8b8f');
      }
      const colors={copper:['#aa693c','#e3ad64','#f9dc9c'],gold:['#b7852d','#f3c34a','#ffe69b'],
        emerald:['#177947','#34d36b','#a7f6a5'],diamond:['#1d858d','#54dccc','#c4fff0']}[type];
      if(colors)for(const [x,y] of [[3,4],[12,3],[8,11],[15,15],[2,16]]) {
        p(x-1,y,4,3,'#637576');p(x,y,3,2,colors[0]);p(x,y,2,1,colors[1]);p(x,y-1,1,1,colors[2]);
      }
    }
    if(type==='tnt') {
      p(2,2,16,16,'#252627');p(3,3,14,14,'#c2392d');
      for(let i=0;i<3;i++){p(4+i*4,4,2,4,'#fc8054');p(4+i*4,13,2,3,'#f46c3a')}
      p(3,9,14,3,'#fff0c9');p(5,9,2,2,'#453831');p(9,9,1,3,'#453831');p(12,9,2,2,'#453831');
      p(9,12,2,5,'#e2a339');
    }
    if(type==='heart'||type==='boost') {
      p(2,2,16,16,'#725335');p(3,3,14,14,'#b18041');p(4,4,12,1,'#d0a461');
      if(type==='heart') {
        p(6,7,3,2,'#ff7182');p(11,7,3,2,'#ff7182');p(5,9,10,3,'#f04461');
        p(7,12,6,2,'#ef3d57');p(9,14,2,1,'#b62b43');p(6,8,2,1,'#ffa2a6');
      } else {
        p(8,9,4,6,'#57b936');p(6,8,8,2,'#71d53e');p(8,6,4,2,'#82e34d');p(9,5,2,1,'#a8f576');
      }
    }
    tiles.set(key,canvas);return canvas;
  }
  function sizeWorld() {
    const scene=world.parentElement;
    if(!scene.clientWidth||!scene.clientHeight)return;
    // The canvas is 120% wide; preserve square pixels with the same vertical scale.
    const h=Math.round(200*scene.clientHeight/scene.clientWidth);
    if(world.width!==240||world.height!==h){world.width=240;world.height=h;ctx.imageSmoothingEnabled=false}
    const size=`${scene.clientWidth}:${scene.clientHeight}`;
    if(state.sceneSize!==size) {
      state.sceneSize=size;
      state.controlHeight=$('game-controls').offsetHeight || 210;
      scene.style.setProperty('--control-height',`${state.controlHeight}px`);
    }
  }
  function blockAt(row,col,replay,time) {
    if(row<physics.FIRST_ROW)return 'sky';
    const minedAt=replay?.mined.get(`${row}:${col}`);
    if(minedAt!==undefined&&time>=minedAt)return 'sky';
    const hit=replay?.byRow.get(row);
    if(hit&&col===hit.col)return hit.kind;
    if(row<physics.FIRST_ROW+2)return 'dirt';
    const coarse=rand((Math.floor(row/2)+17)*12791+(Math.floor(col/2)+9)*6239)();
    if(coarse>.84&&row<35)return 'dirt';
    if(coarse<.065)return row>16?'emerald':'copper';
    if(coarse<.12)return 'gold';
    if(coarse>.78&&coarse<.80&&row>24)return 'diamond';
    if(coarse>.27&&coarse<.48)return 'cobble';
    return 'stone';
  }
  function cloud(x,y,w=30) {
    x=Math.round(x);y=Math.round(y);
    ctx.fillStyle='#e7f2fb';ctx.fillRect(x,y,w,3);ctx.fillRect(x+5,y-3,w-10,3);ctx.fillRect(x+10,y-5,w-20,2);
    ctx.fillStyle='#c9def4';ctx.fillRect(x+4,y+3,w-8,1);
  }
  function portalY() {
    return Math.max(78,Math.min(world.height*.31,world.height-147));
  }
  function focusLine() {
    const ratio=world.height/Math.max(1,world.parentElement.clientHeight);
    return Math.max(68,(world.height-state.controlHeight*ratio)*.65);
  }
  function initialCamera() {return 38-focusLine()}
  function drawBackdrop(time,camera=initialCamera()) {
    const H=world.height, travel=Math.max(0,camera-initialCamera());
    const sky=ctx.createLinearGradient(0,0,0,H);
    sky.addColorStop(0,'#89b2e5');sky.addColorStop(1,'#b6d3ee');
    ctx.fillStyle=sky;ctx.fillRect(0,0,240,H);
    cloud(24+Math.sin(time*.12)*9,H*.18-travel*.65,34);
    cloud(157+Math.sin(time*.1+1)*8,H*.33-travel*.65,43);
    // Floating grassy island, kept behind the foreground like the reference.
    const y=Math.round(H*.43-travel*.7);
    if(y>-28) {
      ctx.fillStyle='#85bc50';ctx.fillRect(174,y,39,3);
      ctx.fillStyle='#599241';ctx.fillRect(174,y+3,39,2);
      ctx.fillStyle='#9ca897';
      for(let i=0;i<8;i++)ctx.fillRect(176+i*5,y+5,2,5+(i*7)%15);
      ctx.fillStyle='#815d3b';ctx.fillRect(191,y-12,2,12);
      ctx.fillStyle='#70af43';ctx.fillRect(186,y-20,12,9);ctx.fillRect(189,y-23,6,4);
    }
  }
  function drawTerrain(camera,replay=null,time=0) {
    const start=Math.max(physics.FIRST_ROW,Math.floor(camera/20)-1);
    const end=Math.ceil((camera+world.height)/20)+1;
    for(let row=start;row<=end;row++)for(let col=0;col<12;col++) {
      const kind=blockAt(row,col,replay,time);
      if(kind==='sky')continue;
      const y=Math.round(row*20-camera);
      ctx.drawImage(tile(kind,((row*3+col*7)%3+3)%3),col*20,y);
      if(row===physics.FIRST_ROW) {
        ctx.fillStyle='#83c24b';ctx.fillRect(col*20,y,20,2);
        ctx.fillStyle='#4f9139';ctx.fillRect(col*20,y+2,20,1);
      }
    }
  }
  function portalPath(x,y,w,h,corner) {
    ctx.beginPath();ctx.moveTo(x+corner,y);ctx.lineTo(x+w-corner,y);
    ctx.lineTo(x+w,y+corner);ctx.lineTo(x+w,y+h-corner);
    ctx.lineTo(x+w-corner,y+h);ctx.lineTo(x+corner,y+h);
    ctx.lineTo(x,y+h-corner);ctx.lineTo(x,y+corner);ctx.closePath();
  }
  function drawPortal(y,time,scale=1,reel=null,offset=0,type=state.visualPick) {
    ctx.save();ctx.translate(120,Math.round(y));ctx.scale(scale,scale);
    portalPath(-44,-46,88,92,12);ctx.fillStyle='#34424a';ctx.fill();
    ctx.save();ctx.clip();
    for(let row=0;row<6;row++)for(let col=0;col<6;col++)
      ctx.drawImage(tile('cobble',(row+col)%3),-44+col*16,-46+row*16,16,16);
    const moss=rand(713);
    for(let i=0;i<35;i++) {
      ctx.fillStyle=i%3?'#72ab45':'#43813d';
      ctx.fillRect(-35+(moss()*69|0),-46+(moss()*14|0),3+(i%4),2+(i%3));
    }
    ctx.restore();
    portalPath(-32,-34,64,68,6);ctx.fillStyle='#526f83';ctx.fill();
    portalPath(-29,-31,58,62,5);ctx.fillStyle='#96b9db';ctx.fill();
    ctx.save();ctx.clip();
    ctx.fillStyle='#86adcf';ctx.fillRect(-27,2,54,26);
    if(reel) {
      const center=Math.floor(offset);
      for(let i=Math.max(0,center-1);i<=Math.min(reel.length-1,center+1);i++)
        pixelPick(ctx,0,(i-offset)*reelPitch-2,1.75,-.15,reel[i]);
    } else if(type) pixelPick(ctx,0,-2+Math.sin(time*2)*.8,1.75,-.15,type);
    ctx.restore();
    for(const x of [-38,36]) {
      ctx.fillStyle='#8e602f';ctx.fillRect(x,-1,3,13);
      ctx.fillStyle='#e4a92e';ctx.fillRect(x-1,-6,5,8);
      ctx.fillStyle='#fff29b';ctx.fillRect(x,-8,3,5);
    }
    ctx.globalAlpha=.65+Math.sin(time*4)*.25;
    ctx.fillStyle='#fff59e';ctx.fillRect(-17,-40,2,2);ctx.fillRect(24,35,2,2);
    if(state.revealPlayed && state.running && state.elapsed<rollDuration) {
      ctx.globalAlpha=.35+Math.sin(time*9)*.15;
      portalPath(-44,-46,88,92,12);ctx.strokeStyle='#c6f600';ctx.lineWidth=1;ctx.stroke();
    }
    ctx.restore();
  }
  function launchPosition(p) {
    const first=state.replay.frames[0], seconds=launchDuration/1000;
    const start=portalY()-2, target=focusLine(), turn=p*p*(p-1);
    // Match the first physics frame's position and velocity at the handoff.
    return {x:120-first.vx*seconds/Math.PI*Math.sin(Math.PI*p),
      y:start+(target-start)*smooth(p)+first.vy*seconds*turn,
      angle:-.15+Math.PI*2*smooth(p)+first.omega*seconds*turn,
      scale:1.75-.1*smooth(p)};
  }
  function drawIdle(time=0) {
    drawBackdrop(time);drawTerrain(initialCamera(),state.running?state.replay:null);
    const rolling=state.running && state.reel && state.elapsed<rollDuration;
    if(rolling) {
      const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const zoom=reduced?0:smooth(clamp(state.elapsed/spinStart))*(1-smooth(clamp((state.elapsed-zoomOutStart)/(rollDuration-zoomOutStart))));
      const ratio=world.height/world.parentElement.clientHeight;
      const maxScale=clamp((world.parentElement.clientHeight-state.controlHeight-116)*ratio/92,1,1.78);
      const expandedY=112*ratio+46*maxScale;
      const center=portalY()+zoom*(expandedY-portalY());
      drawPortal(center,time,1+zoom*(maxScale-1),state.reel,rollPosition());
    } else if(state.running && state.reel && state.elapsed>=rollDuration) {
      drawPortal(portalY(),time,1,null,null,null);
      const p=clamp((state.elapsed-rollDuration)/launchDuration), body=launchPosition(p);
      pixelPick(ctx,body.x,body.y,body.scale,body.angle,state.pickaxe);
    } else drawPortal(portalY(),time);
  }
  function drawHealth(x,y,hp) {
    const text=`${hp}/${state.maxHp}`,width=text.length*6+12;
    const hx=Math.max(7,Math.min(233-width,Math.round(x-width/2)));
    ctx.fillStyle='#19232b';ctx.fillRect(hx-1,y-1,10,6);ctx.fillRect(hx+1,y+5,6,2);ctx.fillRect(hx+3,y+7,2,1);
    ctx.fillStyle='#f15b72';ctx.fillRect(hx,y,3,3);ctx.fillRect(hx+5,y,3,3);ctx.fillRect(hx,y+2,8,3);
    ctx.fillRect(hx+2,y+5,4,2);ctx.fillRect(hx+3,y+7,2,1);ctx.fillStyle='#ff95a0';ctx.fillRect(hx+1,y,1,2);
    pixelText(ctx,text,hx+12,y,1,'#f8fcf5','#182227');
  }
  function drawWorld(progress=0,time=state.time||0) {
    if(state.view!=='game')return;
    sizeWorld();const H=world.height;
    if(!state.round||!state.running||!state.replay||!state.body){drawIdle(time);return}
    if(state.elapsed<flightStart){drawIdle(time);return}
    const steps=state.round.steps;
    const body=state.body,playerY=Math.round(body.y-body.focusY+focusLine());
    const camera=body.focusY-focusLine();
    ctx.save();
    if(time<state.shakeUntil)ctx.translate(Math.round(Math.sin(time*90)*1.3),Math.round(Math.cos(time*70)));
    drawBackdrop(time,camera);drawTerrain(camera,state.replay,state.simulationTime);
    const portalScreenY=portalY()+initialCamera()-camera;
    if(portalScreenY>-60)drawPortal(portalScreenY,time,1,null,null,null);
    for (const p of state.particles || []) {
      ctx.globalAlpha=Math.min(1,p.life*3);ctx.fillStyle=p.color;
      ctx.fillRect(Math.round(p.x),Math.round(p.y-camera),p.size,p.size);
    }
    ctx.globalAlpha=1;pixelPick(ctx,body.x,playerY,1.65,body.angle,state.pickaxe);ctx.restore();
    if(state.running)drawHealth(body.x,playerY-40,state.step<0?state.maxHp:steps[state.step].hp);
  }

  async function init() {
    drawMark(); updateBet();
    if(tg){tg.ready();tg.expand();tg.setHeaderColor?.('#000000');tg.setBackgroundColor?.('#000000');
      tg.BackButton?.onClick(()=>{if(account.active){account.close();return}$('help-modal').classList.contains('hidden')?showView('home'):closeHelp()})}
    try {
      const data=await jsonFetch('/api/me'),{user,pickaxes:serverPickaxes}=data;
      if (!user) throw new Error('API игры недоступен');
      if (Array.isArray(serverPickaxes) && serverPickaxes.length === 3 &&
          serverPickaxes.every(p => palettes[p.id] && Number.isFinite(p.chance) && Number.isFinite(p.max_hp))) {
        pickaxes = serverPickaxes; drawMark();
      }
      state.user=user;state.balance=user.balance;updateUser();$('fatal-error').classList.add('hidden');
      account.configure(data);account.showWelcome();
    } catch(err) {
      const local = ['127.0.0.1','localhost'].includes(location.hostname) || location.protocol==='file:';
      if(local && !tg?.initData && (err.status===0 || err.status===404 || err instanceof TypeError || err.message==='API игры недоступен')) {
        state.preview=true;
        state.user={id:0,name:'Игрок',username:'',photo_url:'',balance:0,welcome_prize:0,welcome_available:true};
        state.balance=0;
        updateUser();$('fatal-error').classList.add('hidden');
        account.configure({});account.showWelcome();
      } else {
        $('fatal-message').textContent=err.message;$('fatal-error').classList.remove('hidden');
      }
    }
    updateBet();
  }
  $('retry-button').addEventListener('click',init);
  $('account-bar').addEventListener('click',()=>showView('profile'));
  $('start-game').addEventListener('click',()=>showView('game'));
  $('game-user').addEventListener('click',()=>showView('profile'));
  $('game-back').addEventListener('click',()=>showView('home'));
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view)));
  const pickaxeBet = window.PixelBet.create({input:$('bet-value'),minus:$('bet-minus'),plus:$('bet-plus'),max:$('bet-max'),
    change:()=>{updateBet();sound?.play('start',1.15)}});
  $('speed-button').addEventListener('click',()=>{state.fast=!state.fast;$('speed-button').classList.toggle('active',state.fast);$('speed-button').setAttribute('aria-pressed',String(state.fast));sound?.play('start',1.15)});
  const mines=window.PixelMines.create({$,sound,tg,money,requestId,fetch:jsonFetch,
    preview:()=>state.preview,balance:()=>state.balance,user:()=>state.user,
    blocked:()=>!state.user||state.requestPending||state.running,
    busy:value=>{state.requestPending=value;updateBet()},
    account:user=>{if(user){state.user=user;state.balance=user.balance;updateUser()}},
    history:round=>state.previewHistory.unshift(round)});
  const account=window.PickaxeAccount.create({$,sound,tg,money,requestId,fetch:jsonFetch,
    user:()=>state.user,balance:()=>state.balance,local:()=>state.preview,view:()=>state.view,
    blocked:()=>!state.user||state.requestPending||state.running,
    busy:value=>{state.requestPending=value;updateBet()},
    account:user=>{state.user=user;state.balance=user.balance;updateUser()},
    modalChanged:clearViewTransition});
  function updateSoundButton() {
    const muted = sound?.muted || false;
    for(const id of ['sound-button','mines-sound']) {
      $(id).classList.toggle('muted', muted);
      $(id).setAttribute('aria-pressed', String(!muted));
      $(id).setAttribute('aria-label', muted ? 'Включить звук' : 'Выключить звук');
    }
  }
  $('sound-button').addEventListener('click',()=>{sound?.setMuted(!sound.muted);updateSoundButton();sound?.play('start')});
  $('mines-sound').addEventListener('click',()=>{sound?.setMuted(!sound.muted);updateSoundButton();sound?.play('start')});
  updateSoundButton();
  $('spin-button').addEventListener('click',play);$('repeat-button').addEventListener('click',play);
  document.querySelectorAll('[data-help]').forEach(button=>button.addEventListener('click',openHelp));
  $('help-close').addEventListener('click',closeHelp);
  $('help-play').addEventListener('click',()=>{closeHelp();showView('game')});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeHelp()});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopRenderer();else {startRenderer();refreshUser()}});
  window.addEventListener('resize',()=>{if(state.view==='game'){sizeWorld();updateUser();setAmount('game-win',state.running?state.displayedWin:state.lastWin);drawWorld(state.progress)}});
  const carousel=$('promo-carousel'), dots=[...document.querySelectorAll('[data-slide]')];
  dots.forEach(button=>button.addEventListener('click',()=>carousel.scrollTo({left:Number(button.dataset.slide)*carousel.clientWidth,
    behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})));
  carousel.addEventListener('scroll',()=> {
    const selected=Math.round(carousel.scrollLeft/Math.max(1,carousel.clientWidth));
    dots.forEach((button,i)=>{button.classList.toggle('selected',i===selected);button.setAttribute('aria-pressed',String(i===selected))});
  },{passive:true});
  carousel.addEventListener('keydown',event=> {
    if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
    event.preventDefault();const next=clamp(Math.round(carousel.scrollLeft/carousel.clientWidth)+(event.key==='ArrowRight'?1:-1),0,dots.length-1);
    dots[next].click();
  });
  init();
})();
