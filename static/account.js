(() => {
  'use strict';
  function drawWheel(canvas, prizes) {
    const c=canvas.getContext('2d'), size=canvas.width, center=size/2, radius=center-10;
    c.clearRect(0,0,size,size);
    const colors=['#c6f600','#f0f7c0','#79ab35','#d6ed72'];
    c.save();c.translate(center,center);
    prizes.forEach((prize,i)=> {
      const start=-Math.PI/2+i*Math.PI*2/prizes.length, end=start+Math.PI*2/prizes.length;
      c.beginPath();c.moveTo(0,0);c.arc(0,0,radius,start,end);c.closePath();
      c.fillStyle=colors[i%colors.length];c.fill();c.strokeStyle='#253b20';c.lineWidth=3;c.stroke();
      c.save();c.rotate((start+end)/2);
      c.fillStyle='#203a18';c.textAlign='center';c.textBaseline='middle';c.font='800 33px Inter';
      c.translate(radius*.64,0);c.rotate(Math.PI/2);c.fillText(new Intl.NumberFormat('ru-RU').format(prize)+' ₽',0,0);
      c.restore();
    });
    c.beginPath();c.arc(0,0,radius,0,Math.PI*2);c.strokeStyle='#d8f56a';c.lineWidth=12;c.stroke();
    for(let i=0;i<32;i++) {
      const angle=i*Math.PI/16;c.save();c.rotate(angle);c.fillStyle=i%2?'#f8ffd8':'#71a724';
      c.fillRect(-3,-radius-3,6,6);c.restore();
    }
    c.restore();
  }
  function create(deps) {
    const $=deps.$, sound=deps.sound, tg=deps.tg;
    let manager='',prizes=[500,1000,3000,5000],walletOpen=false,welcomeOpen=false;
    let walletBusy=false,welcomeBusy=false,kind='deposit',savedRequest=null,submitId=null,previousFocus=null;
    let award=null,completionTimer=0;
    const reduced=()=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    function modalChanged() {
      document.body.style.overflow=walletOpen||welcomeOpen?'hidden':'';
      deps.modalChanged();
      if(walletOpen||welcomeOpen)tg?.BackButton?.show();
      else if(deps.view()==='home')tg?.BackButton?.hide();
    }
    function sync() {
      const user=deps.user();
      $('welcome-open').classList.toggle('hidden',!user?.welcome_available);
      $('profile-gift').textContent=user?.welcome_prize?`Колесо принесло ${deps.money(user.welcome_prize)} ₽`:'Бесплатное колесо — до 5 000 ₽ на старт';
    }
    function configure(data) {
      manager=typeof data.manager_username==='string'?data.manager_username:'';
      if(Array.isArray(data.welcome_rewards)&&data.welcome_rewards.length===4)prizes=data.welcome_rewards;
      drawWheel($('welcome-wheel'),prizes);sync();
      document.fonts?.ready?.then(()=>drawWheel($('welcome-wheel'),prizes));
    }
    function showWelcome() {
      if(!deps.user()?.welcome_available||walletBusy||welcomeBusy)return;
      if(walletOpen)close();previousFocus=document.activeElement;
      welcomeOpen=true;award=null;
      const wheel=$('welcome-wheel');wheel.style.transition='none';wheel.style.transform='rotate(0deg)';
      $('welcome-title').textContent='Колесо фортуны';
      $('welcome-description').innerHTML='От 500 до 5 000 ₽ на старт.<br>Твоя награда уже ждёт.';
      $('welcome-prize').classList.add('hidden');$('welcome-error').textContent='';
      $('welcome-action').textContent='Забрать бесплатно';$('welcome-action').disabled=false;
      $('welcome-close').disabled=false;$('welcome-modal').classList.remove('hidden');
      drawWheel(wheel,prizes);modalChanged();$('welcome-action').focus();
    }
    function showWallet(nextKind) {
      if(!deps.user()||deps.blocked()||welcomeBusy||walletBusy)return;
      close();previousFocus=document.activeElement;kind=nextKind==='withdraw'?'withdraw':'deposit';
      walletOpen=true;savedRequest=null;submitId=null;
      $('wallet-modal').setAttribute('aria-labelledby','wallet-title');
      $('wallet-title').textContent=kind==='deposit'?'Пополнение':'Вывод';
      $('wallet-description').textContent=kind==='deposit'?'Укажи сумму пополнения':`Доступно ${deps.money(deps.balance())} ₽`;
      $('wallet-submit').textContent=kind==='deposit'?'Пополнить':'Вывести';
      $('wallet-submit').disabled=false;$('wallet-close').disabled=false;$('wallet-amount').value='';
      $('wallet-error').textContent='';$('wallet-form-step').classList.remove('hidden');
      $('wallet-contact-step').classList.add('hidden');$('wallet-copy-text').classList.add('hidden');
      $('wallet-modal').classList.remove('hidden');modalChanged();$('wallet-amount').focus();
    }
    function close() {
      if(walletBusy||welcomeBusy)return false;
      const wasOpen=walletOpen||welcomeOpen;
      walletOpen=welcomeOpen=false;$('wallet-modal').classList.add('hidden');$('welcome-modal').classList.add('hidden');
      if(wasOpen){modalChanged();previousFocus?.focus?.()}
      return wasOpen;
    }
    function announce() {
      if(!award)return;
      deps.account(award.user);welcomeBusy=false;deps.busy(false);
      $('welcome-prize').textContent=`+${deps.money(award.prize)} ₽`;$('welcome-prize').classList.remove('hidden');
      $('welcome-title').textContent='Твоя награда!';$('welcome-description').textContent='Уже на балансе. Пора за находками.';
      $('welcome-action').textContent='Играть';$('welcome-action').disabled=false;$('welcome-close').disabled=false;
      sound?.play('finish');tg?.HapticFeedback?.notificationOccurred('success');sync();
    }
    async function claimWelcome() {
      if(welcomeBusy)return;
      if(award){close();return}
      welcomeBusy=true;deps.busy(true);$('welcome-action').disabled=true;$('welcome-close').disabled=true;
      $('welcome-action').textContent='Крутим…';$('welcome-error').textContent='';sound?.unlock();sound?.play('start');
      try {
        if(deps.local()) {
          const user={...deps.user()};
          const prize=user.welcome_prize||prizes[Math.random()*prizes.length|0]*100;
          if(!user.welcome_prize)user.balance=deps.balance()+prize;
          user.welcome_prize=prize;user.welcome_available=false;award={prize,user};
        } else {
          try {award=await deps.fetch('/api/welcome/claim',{method:'POST',body:'{}'})}
          catch(error) {
            const data=await deps.fetch('/api/me');
            if(!data.user?.welcome_prize)throw error;
            award={prize:data.user.welcome_prize,user:data.user};
          }
        }
        const index=Math.max(0,prizes.indexOf(award.prize/100)),wheel=$('welcome-wheel');
        void wheel.offsetWidth;
        wheel.style.transition=reduced()?'none':'transform 3.8s cubic-bezier(.12,.74,.12,1)';
        wheel.style.transform=`rotate(${360*6-(index+.5)*360/prizes.length}deg)`;
        clearTimeout(completionTimer);completionTimer=setTimeout(announce,reduced()?0:3900);
      }catch(error) {
        award=null;welcomeBusy=false;deps.busy(false);$('welcome-action').disabled=false;$('welcome-close').disabled=false;
        $('welcome-action').textContent='Забрать бесплатно';$('welcome-error').textContent=error instanceof TypeError?'Нет связи. Попробуй ещё раз.':error.message;
      }
    }
    function showContact(data) {
      savedRequest=data.request;manager=data.manager_username||manager;
      deps.account(data.user);
      $('wallet-form-step').classList.add('hidden');$('wallet-contact-step').classList.remove('hidden');
      $('wallet-modal').setAttribute('aria-labelledby','wallet-contact-title');
      const deposit=savedRequest.kind==='deposit';
      $('wallet-contact-title').textContent=deposit?'Пополнение через менеджера':'Вывод через менеджера';
      $('wallet-contact-description').textContent=deposit?'Напиши менеджеру, чтобы согласовать пополнение.':'Напиши менеджеру, чтобы согласовать вывод.';
      $('wallet-contact-amount').textContent=`${deps.money(savedRequest.amount)} ₽`;
      $('wallet-request-number').textContent=`Заявка № ${savedRequest.id.slice(0,8).toUpperCase()}`;
      $('wallet-manager').textContent=`@${manager}`;
      $('wallet-contact-note').textContent=deposit?'Менеджер подтвердит пополнение после оплаты.':'Сумма зарезервирована до решения менеджера.';
      $('wallet-copy').textContent='Скопировать заявку';$('wallet-manager').focus();
    }
    async function submitWallet(event) {
      event.preventDefault();if(walletBusy||!walletOpen||deps.blocked())return;
      const value=$('wallet-amount').value.trim().replace(',','.');
      if(!/^\d{1,7}(?:\.\d{1,2})?$/.test(value)||Number(value)<1||Number(value)>1000000) {
        $('wallet-error').textContent='Укажи сумму от 1 до 1 000 000 ₽.';return;
      }
      if(kind==='withdraw'&&Math.round(Number(value)*100)>deps.balance()) {
        $('wallet-error').textContent='Сумма вывода превышает баланс.';return;
      }
      if(!manager) {$('wallet-error').textContent='Менеджер пока не указан.';return}
      walletBusy=true;deps.busy(true);$('wallet-submit').disabled=true;$('wallet-close').disabled=true;$('wallet-amount').disabled=true;
      $('wallet-submit').textContent='Подождите…';$('wallet-error').textContent='';
      try {
        if(deps.local())throw new Error('Открой приложение через Telegram, чтобы создать заявку.');
        const data=await deps.fetch('/api/wallet/request',{method:'POST',body:JSON.stringify({kind,amount:value,request_id:submitId||=(deps.requestId())})});
        showContact(data);sound?.play('pickup');
      }catch(error) {
        let recovered=false;
        if(submitId&&!deps.local()) {
          try {const data=await deps.fetch(`/api/wallet?request_id=${encodeURIComponent(submitId)}`);if(data.request){showContact(data);recovered=true}}catch (_) {}
        }
        if(!recovered)$('wallet-error').textContent=error instanceof TypeError?'Нет связи. Повтори действие.':error.message;
      }finally {
        walletBusy=false;deps.busy(false);$('wallet-submit').disabled=false;$('wallet-close').disabled=false;$('wallet-amount').disabled=false;
        $('wallet-submit').textContent=kind==='deposit'?'Пополнить':'Вывести';
      }
    }
    document.querySelectorAll('[data-wallet]').forEach(button=>button.addEventListener('click',()=>showWallet(button.dataset.wallet)));
    $('wallet-form').addEventListener('submit',submitWallet);
    $('wallet-amount').addEventListener('input',()=>{$('wallet-error').textContent=''});
    ['wallet-close','wallet-done','welcome-close'].forEach(id=>$(id).addEventListener('click',close));
    $('welcome-open').addEventListener('click',showWelcome);$('welcome-action').addEventListener('click',claimWelcome);
    ['wallet-modal','welcome-modal'].forEach(id=>$(id).addEventListener('click',event=>{if(event.target===$(id))close()}));
    $('wallet-manager').addEventListener('click',()=> {
      if(!/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(manager))return;
      const link=`https://t.me/${manager}`;
      if(tg?.openTelegramLink)tg.openTelegramLink(link);else window.open(link,'_blank','noopener,noreferrer');
    });
    $('wallet-copy').addEventListener('click',async()=> {
      if(!savedRequest)return;
      const text=`${savedRequest.kind==='deposit'?'Пополнение':'Вывод'}: ${deps.money(savedRequest.amount)} ₽\nЗаявка: ${savedRequest.id}`;
      try {await navigator.clipboard.writeText(text);$('wallet-copy').textContent='Заявка скопирована'}
      catch (_) {$('wallet-copy-text').textContent=text;$('wallet-copy-text').classList.remove('hidden')}
    });
    document.addEventListener('keydown',event=> {
      if(!walletOpen&&!welcomeOpen)return;
      if(event.key==='Escape'){event.preventDefault();close()}
      if(event.key==='Tab') {
        const panel=$(walletOpen?'wallet-modal':'welcome-modal');
        const focusable=[...panel.querySelectorAll('button:not(:disabled),input:not(:disabled)')].filter(e=>e.getClientRects().length);
        if(!focusable.length)return;
        const first=focusable[0],last=focusable[focusable.length-1];
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
      }
    });
    return {configure,sync,showWelcome,close,get active(){return walletOpen||welcomeOpen}};
  }
  window.PickaxeAccount={create,drawWheel};
})();
