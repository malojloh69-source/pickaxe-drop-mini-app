const {test}=require('node:test');
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function harness(fetch,user={balance:0,welcome_prize:0,welcome_available:true}) {
  const elements=new Map(),timers=[],events={},opened=[];
  const context=new Proxy({},{get:(object,key)=>key in object?object[key]:()=>{},set:(object,key,value)=>{object[key]=value;return true}});
  const doc={body:{style:{}},activeElement:null,querySelectorAll:selector=>selector==='[data-wallet]'?buttons:[],
    addEventListener:(event,handler)=>{events[event]=handler}};
  function element(){const listeners={},classes=new Set();return {value:'',style:{},width:512,disabled:false,textContent:'',dataset:{},
    classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),toggle(k,on){on?classes.add(k):classes.delete(k)},contains:k=>classes.has(k)},
    setAttribute(k,v){this[k]=v},getContext:()=>context,focus(){doc.activeElement=this},
    addEventListener(k,f){listeners[k]=f},emit(k,event={}){return listeners[k]?.({preventDefault(){},...event})},
    click(){if(!this.disabled)return this.emit('click')},querySelectorAll:()=>[]};}
  const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  const buttons=['deposit','withdraw'].map(kind=>{const button=element();button.dataset.wallet=kind;return button});
  const window={matchMedia:()=>({matches:false}),open:(url)=>opened.push(url)};
  const sandbox=vm.createContext({window,document:doc,Intl,console,navigator:{clipboard:{writeText:async()=>{}}},
    setTimeout:f=>{timers.push(f);return timers.length},clearTimeout(){}});
  vm.runInContext(fs.readFileSync('static/account.js','utf8'),sandbox);
  let busy=false,modalChanges=0;
  const account=window.PickaxeAccount.create({$,fetch,user:()=>user,balance:()=>user.balance,local:()=>false,
    view:()=> 'home',blocked:()=>busy,busy:value=>{busy=value},account:value=>{user=value},
    modalChanged:()=>modalChanges++,money:value=>String(value/100),requestId:()=> 'request-one'});
  return {account,$,buttons,opened,configure:()=>account.configure({manager_username:'Valid_Manager',welcome_rewards:[500,1000,3000,5000]}),
    finishAnimation:()=>{timers.splice(0).forEach(f=>f())},get user(){return user},get busy(){return busy},get modalChanges(){return modalChanges}};
}
async function settle(){await new Promise(resolve=>setImmediate(resolve))}

test('welcome award waits for the wheel, prevents duplicate clicks and cannot be claimed again',async()=>{
  let requests=0;
  const ui=harness(async()=>{requests++;return {prize:300000,user:{balance:300000,welcome_prize:300000,welcome_available:false}}});
  ui.configure();ui.account.showWelcome();
  const claim=ui.$('welcome-action').click();ui.$('welcome-action').click();await claim;
  assert.equal(requests,1);assert.equal(ui.user.balance,0);assert.equal(ui.busy,true);
  assert.equal(ui.account.close(),false);
  assert.equal(ui.$('welcome-wheel').style.transform,'rotate(1935deg)');
  ui.finishAnimation();assert.equal(ui.user.balance,300000);assert.equal(ui.busy,false);
  assert.equal(ui.$('welcome-prize').textContent,'+3000 ₽');
  ui.$('welcome-action').click();assert.equal(ui.account.active,false);
  ui.account.showWelcome();assert.equal(ui.account.active,false);
});

test('a lost welcome response restores the credited server award once',async()=>{
  const ui=harness(async(url)=>{
    if(url.includes('/claim'))throw new TypeError('connection lost');
    return {user:{balance:500000,welcome_prize:500000,welcome_available:false}};
  });
  ui.configure();ui.account.showWelcome();await ui.$('welcome-action').click();ui.finishAnimation();
  assert.equal(ui.user.balance,500000);assert.equal(ui.$('welcome-prize').textContent,'+5000 ₽');
});

test('deposit opens the amount sheet then the configured manager sheet without crediting money',async()=>{
  let sent;
  const ui=harness(async(url,options)=>{sent=JSON.parse(options.body);return {request:{id:'abc12345-full',kind:'deposit',amount:12345},
    manager_username:'Valid_Manager',user:{balance:50000,welcome_prize:50000,welcome_available:false}}},
    {balance:50000,welcome_prize:50000,welcome_available:false});
  ui.configure();ui.buttons[0].click();assert.equal(ui.account.active,true);
  ui.$('wallet-amount').value='123,45';await ui.$('wallet-form').emit('submit');
  assert.equal(sent.kind,'deposit');assert.equal(sent.amount,'123.45');assert.equal(ui.user.balance,50000);
  assert.equal(ui.$('wallet-contact-title').textContent,'Пополнение через менеджера');
  assert.equal(ui.$('wallet-manager').textContent,'@Valid_Manager');
  assert.equal(ui.$('wallet-form-step').classList.contains('hidden'),true);
  ui.$('wallet-manager').click();assert.equal(ui.opened[0],'https://t.me/Valid_Manager');
});

test('withdrawal rejects an amount above balance and recovers a lost reservation response',async()=>{
  let requests=0;
  const ui=harness(async(url)=>{
    requests++;
    if(url.includes('/request'))throw new TypeError('lost response');
    return {request:{id:'withdraw-1',kind:'withdraw',amount:20000},manager_username:'Valid_Manager',
      user:{balance:30000,welcome_prize:50000,welcome_available:false}};
  },{balance:50000,welcome_prize:50000,welcome_available:false});
  ui.configure();ui.buttons[1].click();ui.$('wallet-amount').value='501';await ui.$('wallet-form').emit('submit');
  assert.equal(requests,0);assert.match(ui.$('wallet-error').textContent,/превышает/);
  ui.$('wallet-amount').value='200';await ui.$('wallet-form').emit('submit');
  assert.equal(requests,2);assert.equal(ui.user.balance,30000);
  assert.equal(ui.$('wallet-contact-title').textContent,'Вывод через менеджера');
  assert.equal(ui.$('wallet-contact-note').textContent,'Сумма зарезервирована до решения менеджера.');
});

test('missing manager configuration never creates a wallet request',async()=>{
  let called=false;const ui=harness(async()=>{called=true}, {balance:50000,welcome_available:false});
  ui.account.configure({});ui.buttons[0].click();ui.$('wallet-amount').value='100';await ui.$('wallet-form').emit('submit');
  assert.equal(called,false);assert.equal(ui.$('wallet-error').textContent,'Менеджер пока не указан.');
});
