const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const {randomSeed} = require('../app/web/random_seed.js');
const generated = new Set();
let previous = 831001;
for (let i = 0; i < 1000; i++) {
  const value = randomSeed(previous, webcrypto);
  assert(Number.isSafeInteger(value) && value > 0 && value <= Number.MAX_SAFE_INTEGER);
  assert(!generated.has(value));
  generated.add(value); previous = value;
}
const samples = [[0, 0], [0, 123], [0x1fffff, 0xffffffff]];
const fake = {getRandomValues(words) { words.set(samples.shift()); }};
assert.equal(randomSeed(123, fake), Number.MAX_SAFE_INTEGER);
assert.throws(() => randomSeed(null, null), /安全随机数/);
assert.throws(() => randomSeed(5, {getRandomValues(words) { words.set([0,5]); }}), /新的安全随机种子/);
assert.throws(() => randomSeed(0, {getRandomValues(words) { words.set([0x1fffff,0xffffffff]); }}), /新的安全随机种子/);
console.log('PASS: secure 53-bit seeds, positive bounds, uniqueness, zero/previous rejection and entropy failure.');
