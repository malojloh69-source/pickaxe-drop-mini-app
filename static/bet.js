(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PixelBet = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const limit = Number.MAX_SAFE_INTEGER;
  const presets = [100, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000];
  function parse(raw) {
    const text = String(raw).trim().replace(',', '.');
    if (!/^\d{1,14}(?:\.\d{1,2})?$/.test(text)) return null;
    const [whole, fraction = ''] = text.split('.');
    const cents = BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2));
    return cents >= 100n && cents <= BigInt(limit) ? Number(cents) : null;
  }
  function format(cents) {
    const fraction = cents % 100;
    return String(Math.floor(cents / 100)) + (fraction ? ',' + String(fraction).padStart(2, '0') : '');
  }
  function step(cents, direction, balance = limit) {
    if (direction < 0) {
      const lower = presets.filter(value => value < cents);
      return Math.max(100, cents > presets.at(-1) ? Math.floor(cents / 1.25 / 100) * 100 : lower.at(-1) || 100);
    }
    const higher = presets.find(value => value > cents);
    return Math.min(balance, higher || Math.min(limit, Math.ceil(cents * 1.25 / 100) * 100));
  }
  function create({input, minus, plus, max, change}) {
    let locked = false, balance = 0;
    input.value = '10';
    const cost = () => parse(input.value);
    const set = cents => {input.value = format(cents)};
    const notify = () => change?.();
    input.addEventListener('input', notify);
    input.addEventListener('blur', () => {const value = cost(); if (value !== null) set(value); notify()});
    input.addEventListener('keydown', event => {if(event.key === 'Enter') input.blur()});
    minus.addEventListener('click', () => {if(!locked){set(step(cost() || 1000, -1));notify()}});
    plus.addEventListener('click', () => {if(!locked && balance >= 100){set(step(cost() || 1000, 1, balance));notify()}});
    max.addEventListener('click', () => {if(!locked && balance >= 100){set(balance);notify()}});
    function render(disabled, available) {
      locked = disabled; balance = available;
      const cents = cost(), valid = cents !== null && cents <= balance;
      input.disabled = locked;
      input.setAttribute('aria-invalid', String(!locked && !valid));
      input.style.fontSize = `${Math.max(13, Math.min(24, 192 / Math.max(8, input.value.length)))}px`;
      minus.disabled = locked || cents === 100;
      plus.disabled = locked || balance < 100 || (cents !== null && cents >= balance);
      max.disabled = locked || balance < 100;
      return valid;
    }
    return {render, set, get cost(){return cost()}, get value(){return cost() === null ? null : format(cost()).replace(',', '.')}};
  }
  return {parse, format, step, create};
});
