(function (root, factory) {
  const api = factory(root.PixelBet || (typeof module === 'object' && module.exports ? require('./bet.js') : null));
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PixelMines = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, (Bet) => {
  'use strict';
  function combination(n, k) {
    let result = 1;
    for (let i = 1; i <= k; i++) result = result * (n - i + 1) / i;
    return Math.round(result);
  }
  function payout(bet, count, opened) {
    return opened ? Math.max(Math.floor(bet*110/100),Math.floor(bet * 96 * combination(25, opened) / (100 * combination(25-count, opened)))) : 0;
  }
  function drawSprite(ctx, kind, seed=0) {
    ctx.clearRect(0, 0, 32, 32); ctx.imageSmoothingEnabled = false;
    const p = (x,y,w,h,color) => {ctx.fillStyle=color;ctx.fillRect(x,y,w,h)};
    if (kind === 'closed') {
      p(0,0,32,32,'#889e98'); p(2,2,28,27,'#bbcdc2');
      p(2,2,28,2,'#e0eee0'); p(2,4,2,25,'#d4e2d2');
      p(4,27,26,3,'#748f84'); p(28,4,2,23,'#98b2a4');
      for(let i=0;i<9;i++) {
        const x=5+(seed*7+i*11)%20, y=5+(seed*3+i*7)%19;
        p(x,y,3,2,i%2?'#a4bba9':'#cddcc8');
      }
      p(11,11,10,2,'#729b66');p(11,13,2,7,'#729b66');
      p(19,13,2,7,'#729b66');p(11,20,10,2,'#729b66');
      p(14,14,4,5,'#d6f18b');
    } else if (kind === 'gem') {
      p(5,26,22,3,'#95bc75');
      const rows = ['....KKKKKK....','...KHHHHHHK...','..KHLBBBBBLK..',
        '.KHLBBBBBBBLK.','KHLBBBBBBBBBLK','KHLBBBBBTBBBLK',
        'KHLBBBBTTBBBLK','KHLBBBBTTBBBLK','KHLBBBTTTBBBLK',
        '.KLBBTTTTBBLK.','..KLTTTTTBLK..','...KLTTTBLK...',
        '....KLTTLK....','.....KLLK.....','......KK......'];
      const palette={K:'#126448',H:'#dcffb1',L:'#8af367',B:'#3dde6b',T:'#13a65b'};
      rows.forEach((row,y)=>[...row].forEach((v,x)=>{if(palette[v])p(2+x*2,1+y*2,2,2,palette[v])}));
      p(4,5,1,3,'#eeffcb');p(3,6,3,1,'#eeffcb');p(26,25,2,2,'#bcff81');
    } else {
      p(5,27,23,3,'#d3a179');p(13,4,5,3,'#755a3a');
      p(18,3,3,2,'#e27d32');p(21,1,2,2,'#fff295');
      p(12,7,8,2,'#546667');p(8,9,16,2,'#2b4147');
      p(6,11,20,13,'#1d343b');p(8,24,16,3,'#183039');
      p(8,12,5,3,'#788e88');p(10,11,9,2,'#4e6966');
      p(12,17,3,3,'#fae1b0');p(19,17,3,3,'#fae1b0');
      p(16,21,2,2,'#fae1b0');p(14,24,6,1,'#8dada0');
      p(4,22,2,3,'#ff955a');p(27,16,2,4,'#ffb04d');
    }
  }
  function create(deps) {
    const $=deps.$, sound=deps.sound, tg=deps.tg;
    let round=null, pending=false, loading=false, mineCount=3, startId=null, previewMines=[];
    const cells=[];
    const active=()=>round?.status==='active';
    const stake=Bet.create({input:$('mines-bet'),minus:$('mines-bet-minus'),plus:$('mines-bet-plus'),max:$('mines-bet-max'),
      change:()=>{round=null;render();sound?.play('start',1.15)}});
    for(let i=0;i<25;i++) {
      const button=document.createElement('button'), canvas=document.createElement('canvas');
      button.type='button';button.className='mine-cell';button.dataset.cell=i;
      canvas.width=canvas.height=32;canvas.setAttribute('aria-hidden','true');button.append(canvas);
      button.addEventListener('click',()=>reveal(i));cells.push(button);$('mines-board').append(button);
    }
    const multiplier = value => `×${new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value)}`;
    function render() {
      const isActive=active(), disabled=pending||loading||deps.blocked(), cost=round && isActive?round.bet:stake.cost;
      const valid=stake.render(disabled||isActive,deps.balance());
      const count=isActive?round.mine_count:mineCount;
      const opened=new Set(round?.opened||[]), mines=new Set(round?.mines||[]);
      cells.forEach((button,i)=> {
        const revealed=opened.has(i), terminal=round && !isActive;
        const kind=terminal?mines.has(i)?'bomb':'gem':revealed?'gem':'closed';
        button.disabled=disabled||!isActive||revealed;
        button.classList.toggle('opened',revealed && kind==='gem');
        button.classList.toggle('bomb',terminal && mines.has(i));
        button.classList.toggle('unopened',!!terminal && !revealed);
        button.setAttribute('aria-label',`Клетка ${i+1}: ${kind==='closed'?'закрыта':kind==='gem'?'изумруд':'мина'}`);
        drawSprite(button.firstChild.getContext('2d'),kind,i);
      });
      $('mines-found').textContent=`${round?.safe_count||0} / ${25-count}`;
      $('mines-payout').textContent=`${deps.money(round?.payout||0)} ₽`;
      $('mines-next').textContent=round && !isActive?'—':multiplier(round?.next_multiplier||(cost?payout(cost,count,1)/cost:1));
      document.querySelectorAll('[data-mine-count]').forEach(button=> {
        const selected=Number(button.dataset.mineCount)===count;
        button.disabled=disabled||isActive;button.classList.toggle('selected',selected);
        button.setAttribute('aria-pressed',String(selected));
      });
      const action=$('mines-action');
      action.disabled=disabled || (isActive ? !round.safe_count : !valid);
      action.textContent=disabled?'Подождите…':isActive?`Забрать ${deps.money(round.payout)} ₽`:cost===null?'Введи ставку':`Начать игру · ${deps.money(cost)} ₽`;
      $('mines-balance').textContent=`${deps.money(deps.balance())} ₽`;
    }
    function apply(data, effect) {
      const prior=round;round=data.round;startId=null;
      if(round?.status==='active') {
        mineCount=round.mine_count;stake.set(round.bet);
      }
      deps.account(data.user);
      if(round && round.status!=='active' && prior?.status==='active') {
        if(deps.preview())deps.history({id:round.id,game:'mines',bet:round.bet,cost:round.bet,win:round.win,created_at:Date.now()/1000|0});
        if(effect)sound?.play(round.status==='lost'?'blast':'finish');
        tg?.HapticFeedback?.notificationOccurred('success');
      } else if(effect)sound?.play(effect==='reveal'?'ore':'start');
      $('mines-status').textContent=!round?'Выбери ставку и открой изумруды.':round.status==='lost'?
        `Мина! Ставка ${deps.money(round.bet)} ₽ проиграна.`:round.status==='cashed'?`Награда ${deps.money(round.win)} ₽ уже на балансе.`:
        round.safe_count?'Изумруд найден! Забери награду или открой ещё.':'Выбери любую клетку.';
      render();
    }
    function demo(action, cell) {
      const user={...deps.user()};user.balance=deps.balance();
      if(action==='start') {
        const cost=stake.cost;
        if(cost===null)throw new Error('Введи ставку от 1 ₽');
        if(user.balance<cost)throw new Error('Недостаточно игровых ₽');
        previewMines=Array.from({length:25},(_,i)=>i);
        for(let i=24;i>0;i--){const j=Math.random()*(i+1)|0;[previewMines[i],previewMines[j]]=[previewMines[j],previewMines[i]]}
        previewMines=previewMines.slice(0,mineCount);
        user.balance-=cost;
        return {round:{id:startId,bet:cost,mine_count:mineCount,opened:[],safe_count:0,status:'active',payout:0,win:0,
          next_multiplier:payout(cost,mineCount,1)/cost},user};
      }
      const result={...round,opened:[...round.opened]};
      if(action==='reveal') {
        if(previewMines.includes(cell)) {
          const hidden=Array.from({length:25},(_,i)=>i).filter(i=>!result.opened.includes(i)&&!previewMines.includes(i)&&i!==cell);
          if(hidden.length&&(result.safe_count<6||Math.random()<.995)) {
            previewMines=previewMines.filter(i=>i!==cell);previewMines.push(hidden[Math.random()*hidden.length|0]);
          }
        }
        result.opened.push(cell);
        if(previewMines.includes(cell))result.status='lost';
        else result.safe_count++;
      }
      if(action==='cashout'||result.safe_count===25-result.mine_count)result.status='cashed';
      result.payout=payout(result.bet,result.mine_count,result.safe_count);
      result.next_multiplier=result.status==='active'?payout(result.bet,result.mine_count,result.safe_count+1)/result.bet:0;
      if(result.status!=='active') {
        result.win=result.status==='lost'?0:result.payout;result.payout=result.win;result.mines=[...previewMines];
        user.balance+=result.win;user.rounds_count=(user.rounds_count||0)+1;
      }
      return {round:result,user};
    }
    async function request(action, cell) {
      if(pending||loading||deps.blocked())return;
      if(action==='start'&&(stake.cost===null||stake.cost>deps.balance()))return;
      pending=true;deps.busy(true);render();sound?.unlock();
      try {
        const body=action==='start'?{bet:stake.value,mine_count:mineCount,request_id:startId||=(deps.requestId())}:
          {round_id:round.id,...(action==='reveal'?{cell}:{})};
        const data=deps.preview()?demo(action,cell):await deps.fetch(`/api/mines/${action}`,{method:'POST',body:JSON.stringify(body)});
        apply(data,action);
      } catch(error) {
        // Recover from a lost response using persisted server state. Retain a start
        // UUID until acknowledged so retrying can never debit a second stake.
        let recovered=false;
        if(!deps.preview()) {
          try {
            const data=await deps.fetch('/api/mines'+(round?`?round_id=${encodeURIComponent(round.id)}`:''));
            if(data.round) {
              recovered=action==='start'||(action==='cashout'?data.round.status!=='active':data.round.opened.includes(cell));
              apply(data,recovered?action:undefined);
            }
          }catch (_) {}
        }
        if(!recovered)$('mines-status').textContent=error instanceof TypeError?'Нет связи. Повтори действие.':error.message;
      } finally {pending=false;deps.busy(false);render()}
    }
    function reveal(cell) {if(active()&&!round.opened.includes(cell))request('reveal',cell)}
    async function enter() {
      if(pending||loading||deps.blocked()){render();return}
      if(deps.preview()){render();return}
      loading=true;deps.busy(true);render();
      try {const data=await deps.fetch('/api/mines'+(round?`?round_id=${encodeURIComponent(round.id)}`:''));apply(data)}
      catch(error){$('mines-status').textContent=error.message}
      finally {loading=false;deps.busy(false);render()}
    }
    document.querySelectorAll('[data-mine-count]').forEach(button=>button.addEventListener('click',()=> {
      if(active()||pending||loading)return;mineCount=Number(button.dataset.mineCount);round=null;render();sound?.play('start',1.15);
    }));
    $('mines-action').addEventListener('click',()=>{if(active()&&!round.safe_count)return;request(active()?'cashout':'start')});
    render();
    return {enter,render,get active(){return active()}};
  }
  return {create,drawSprite,payout};
});
