const {test} = require('node:test');
const assert = require('node:assert/strict');
const Bet = require('../static/bet.js');

function element() {
  const listeners = {};
  return {value:'',style:{},disabled:false,attributes:{},
    addEventListener(name, handler){listeners[name]=handler},
    setAttribute(name,value){this.attributes[name]=value},
    fire(name){if(!this.disabled) listeners[name]?.({key:''})},blur(){listeners.blur?.()}};
}

test('wagers retain exact kopeks and reject malformed or unsafe amounts', () => {
  assert.equal(Bet.parse('1234,56'),123456);
  assert.equal(Bet.parse('501'),50100);
  assert.equal(Bet.parse('1000.01'),100001);
  assert.equal(Bet.format(123456),'1234,56');
  for(const value of ['0.99','NaN','1e5','-10','1.001','999999999999999']) assert.equal(Bet.parse(value),null);
});

test('manual stake, MAX and controls follow available balance and lock during a round', () => {
  const input=element(),minus=element(),plus=element(),max=element();
  const control=Bet.create({input,minus,plus,max});
  assert.equal(control.render(false,123456),true);
  input.value='600,25';input.fire('input');
  assert.equal(control.cost,60025);assert.equal(control.value,'600.25');
  control.render(false,123456);max.fire('click');
  assert.equal(control.cost,123456);assert.equal(input.value,'1234,56');
  control.render(true,123456);minus.fire('click');max.fire('click');
  assert.equal(control.cost,123456);assert.equal(input.disabled,true);
  control.render(false,123456);input.value='2000';
  assert.equal(control.render(false,123456),false);
  assert.equal(input.attributes['aria-invalid'],'true');
  assert.equal(Bet.step(50000,1,100000),100000);
});
