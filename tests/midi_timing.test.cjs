const assert=require('node:assert/strict');
require('../app/web/midi_timing.js');
const T=globalThis.MidiTiming;
const doc={ppq:480,tempos:[{tick:0,tempo:500000},{tick:1920,tempo:750000}],meters:[{tick:0,numerator:4,denominator:4}],
  settings:{loop_start:1,loop_end:8},tracks:[{notes:[{id:'a',tick:1200,duration:240,pitch:60},{id:'b',tick:4000,duration:160,pitch:62}],
    events:[{tick:2100,message:{type:'control_change'}}]}],source:{format:'audio'}};
const before=structuredClone(doc),after=T.regrid(doc,92);
assert.deepEqual(doc,before,'regrid must never mutate input or a failed edit');
for(let i=0;i<doc.tracks[0].notes.length;i++){
  const a=doc.tracks[0].notes[i],b=after.tracks[0].notes[i];
  assert.ok(Math.abs(T.secondsAt(doc,a.tick)-T.secondsAt(after,b.tick))<.001);
  assert.ok(Math.abs(T.secondsAt(doc,a.tick+a.duration)-T.secondsAt(after,b.tick+b.duration))<.001);
  assert.equal(a.id,b.id,'edited notes and their identities retained');
}
assert.ok(Math.abs(T.secondsAt(doc,2100)-T.secondsAt(after,after.tracks[0].events[0].tick))<.001);
const moved=T.shift(doc,['b'],250);
assert.equal(moved.tracks[0].notes[0].tick,1200);
assert.ok(Math.abs(T.secondsAt(moved,moved.tracks[0].notes[1].tick)-T.secondsAt(doc,4000)-.25)<.001);
assert.throws(()=>T.shift(doc,['a'],-2000));
assert.deepEqual(doc,before,'rejected negative shift must leave all notes unchanged');
assert.deepEqual(T.quantize(doc,['b'],16,0),doc,'zero strength is lossless');
const quantized=T.quantize(doc,['b'],16,100);
assert.equal(quantized.tracks[0].notes[1].tick%120,0);
assert.equal(quantized.tracks[0].notes[1].duration,160,'short/rest articulation must not stretch');
assert.equal(quantized.tracks[0].notes[0].tick,1200,'only selected notes change');
assert.throws(()=>T.quantize(doc,['a'],17,50));
assert.throws(()=>T.regrid(doc,NaN));
for(const tick of [0,1,1200,1920,4000,12000])assert.ok(Math.abs(T.tickAt(doc,T.secondsAt(doc,tick))-tick)<1e-6);
console.log('PASS: original-time tempo conversion, variable tempo, controller times, edit identities, scoped shift, negative bounds, quantization strength, articulation, inverse clock.');
