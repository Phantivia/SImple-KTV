import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const code=fs.readFileSync(new URL('../public/recorder-worklet.js',import.meta.url),'utf8');
function setup(start,end) {
  const events=[];let Recorder;
  const context=vm.createContext({Float32Array,currentFrame:0,
    AudioWorkletProcessor:class { constructor(){this.port={postMessage:m=>events.push(m)};} },
    registerProcessor:(name,ctor)=>{ assert.equal(name,'pcm-recorder');Recorder=ctor; }
  });
  vm.runInContext(code,context);
  const recorder=new Recorder();recorder.port.onmessage({data:{type:'arm',startFrame:start,endFrame:end}});
  function block(frame,hasInput=true){
    context.currentFrame=frame;
    const input=Float32Array.from({length:128},(_,i)=>frame+i+1);
    const output=new Float32Array(128).fill(42);
    assert.equal(recorder.process(hasInput?[[input]]:[],[[output]]),true);
    assert.ok(output.every(x=>x===0),'capture must never monitor mic to output');
  }
  return {recorder,events,block,samples:()=>Array.from(events.filter(e=>e.type==='data').flatMap(e=>Array.from(e.samples)))};
}

test('frame-exact punch capture excludes count-in and end padding',()=>{
  const s=setup(5,270);s.block(0);s.block(128);s.block(256);
  assert.equal(s.samples().length,265);assert.equal(s.samples()[0],6);assert.equal(s.samples().at(-1),270);
  assert.equal(s.events.at(-1).type,'done');assert.equal(s.events.at(-1).total,265);
});
test('count-in emits no microphone PCM and no output',()=>{
  const s=setup(1000,2000);s.block(0);s.block(128);
  assert.equal(s.events.length,0);
});
test('early stop flushes the partial take only once',()=>{
  const s=setup(0,2000);s.block(0);s.block(128);
  s.recorder.port.onmessage({data:{type:'stop'}});s.recorder.port.onmessage({data:{type:'stop'}});s.block(256);
  assert.equal(s.samples().length,256);assert.equal(s.events.filter(e=>e.type==='done').length,1);
});
test('stopping during count-in produces an empty capture rather than old audio',()=>{
  const s=setup(1000,2000);s.block(0);s.recorder.port.onmessage({data:{type:'stop'}});
  assert.equal(s.samples().length,0);assert.equal(s.events.at(-1).total,0);
});
test('chunk boundaries neither duplicate nor lose samples',()=>{
  const s=setup(0,5000);for(let frame=0;frame<5120;frame+=128)s.block(frame);
  assert.deepEqual(s.events.filter(e=>e.type==='data').map(e=>e.samples.length),[4096,904]);
  assert.deepEqual(s.samples(),Array.from({length:5000},(_,i)=>i+1));
});
test('temporarily unavailable input is zero-filled at the correct clock positions',()=>{
  const s=setup(0,256);s.block(0,false);s.block(128);
  assert.ok(s.samples().slice(0,128).every(x=>x===0));assert.equal(s.samples()[128],129);
});
test('a subsequent arm resets buffers and capture length',()=>{
  const s=setup(0,128);s.block(0);
  s.recorder.port.onmessage({data:{type:'arm',startFrame:256,endFrame:384}});s.block(256);
  assert.deepEqual(s.events.filter(e=>e.type==='done').map(e=>e.total),[128,128]);
  assert.equal(s.events.filter(e=>e.type==='data')[1].samples[0],257);
});
