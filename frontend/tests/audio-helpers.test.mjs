import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { clock,esc,noteName } from '../dist/js/api.js';
const code=fs.readFileSync(new URL('../dist/js/engine.js',import.meta.url),'utf8')
  .replace(/^import[^;]*;\s*/gm,'').replace(/^export\s+/gm,'');
const context=vm.createContext({Float32Array,ArrayBuffer,DataView,Blob,Map,Set});
vm.runInContext(code+'\nglobalThis.helpers={encodeWav,gainAt};',context);
const {encodeWav,gainAt}=context.helpers;

test('display clock carries rounded seconds to the next minute',()=>{
  assert.equal(clock(59.96,true),'01:00.0');assert.equal(clock(59.5),'01:00');assert.equal(clock(-4,true),'00:00.0');
});
test('untrusted track labels are HTML-escaped',()=>{
  assert.equal(esc('<img onerror="x">&\''),'&lt;img onerror=&quot;x&quot;&gt;&amp;&#39;');
});
test('MIDI display includes pitch class and octave',()=>{
  assert.equal(noteName(69),'A4');assert.equal(noteName(60),'C4');assert.equal(noteName(61),'C♯4');
});
test('recordings are float32 mono WAV at the actual audio context sample rate',async()=>{
  const blob=encodeWav([new Float32Array([.25,-.5]),new Float32Array([0,1])],4,48000);
  const buf=await blob.arrayBuffer();const d=new DataView(buf);
  assert.equal(buf.byteLength,60);assert.equal(d.getUint16(20,true),3);assert.equal(d.getUint16(22,true),1);
  assert.equal(d.getUint32(24,true),48000);assert.equal(d.getUint32(40,true),16);
  assert.equal(d.getFloat32(44,true),.25);assert.equal(d.getFloat32(48,true),-.5);assert.equal(d.getFloat32(56,true),1);
});
test('automation interpolation is linear in dB, not in amplitude',()=>{
  const t={gain_db:-6,automation:[{time:0,db:-12},{time:2,db:0}]};
  assert.ok(Math.abs(gainAt(t,1)-10**(-12/20))<1e-12);
  assert.ok(Math.abs(gainAt(t,4)-10**(-6/20))<1e-12);
});
test('tracks without automation use their configured gain',()=>{
  assert.equal(gainAt({gain_db:0,automation:[]},30),1);
});
