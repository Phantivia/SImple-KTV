import type { Project, Track } from './types.js';
import { assetURL } from './api.js';

interface PlayingTrack { source:AudioBufferSourceNode; gain:GainNode; pan:StereoPannerNode; offset:number }
export interface Capture { blob:Blob; duration:number; sampleRate:number }

function gainAt(track:Track, time:number):number {
  const points=track.automation;
  let db=points[0]?.db ?? 0;
  for(let i=0;i<points.length;i++) {
    const a=points[i]!; const b=points[i+1];
    if(time>=a.time) db=a.db;
    if(b && time>=a.time && time<b.time) { db=a.db+(b.db-a.db)*(time-a.time)/(b.time-a.time); break; }
  }
  return 10**((track.gain_db+db)/20);
}

function encodeWav(chunks:Float32Array[], frames:number, rate:number):Blob {
  const buffer=new ArrayBuffer(44+frames*4); const view=new DataView(buffer);
  const text=(at:number,s:string)=>{ for(let i=0;i<s.length;i++) view.setUint8(at+i,s.charCodeAt(i)); };
  text(0,'RIFF'); view.setUint32(4,36+frames*4,true); text(8,'WAVE'); text(12,'fmt ');
  view.setUint32(16,16,true); view.setUint16(20,3,true); view.setUint16(22,1,true);
  view.setUint32(24,rate,true); view.setUint32(28,rate*4,true); view.setUint16(32,4,true); view.setUint16(34,32,true);
  text(36,'data'); view.setUint32(40,frames*4,true);
  let index=44;
  for(const chunk of chunks) for(const sample of chunk) { view.setFloat32(index,sample,true); index+=4; }
  return new Blob([buffer],{type:'audio/wav'});
}

export class AudioEngine {
  context:AudioContext|null=null;
  analyser:AnalyserNode|null=null;
  micAnalyser:AnalyserNode|null=null;
  master:GainNode|null=null;
  private monitor:GainNode|null=null;
  private limiter:DynamicsCompressorNode|null=null;
  private micSource:MediaStreamAudioSourceNode|null=null;
  private recorder:AudioWorkletNode|null=null;
  private stream:MediaStream|null=null;
  private sources=new Map<string,PlayingTrack>();
  private cache=new Map<string,AudioBuffer>();
  private clickSources:AudioScheduledSourceNode[]=[];
  private chunks:Float32Array[]=[];
  private finishCapture:((capture:Capture)=>void)|null=null;
  private arming=false;
  private captureCancelled=false;
  monitoring=false;
  volume=.8;
  playing=false;
  recording=false;
  micReady=false;
  startAt=0;
  startPosition=0;
  endPosition=0;
  parked=0;
  capturedSeconds=0;
  microphoneName='未连接';
  onDeviceLost:(()=>void)|null=null;

  async init() {
    if(!this.context) {
      this.context=new AudioContext({latencyHint:'interactive',sampleRate:48000});
      this.master=this.context.createGain(); this.master.gain.value=this.volume;
      this.analyser=this.context.createAnalyser(); this.analyser.fftSize=2048; this.analyser.smoothingTimeConstant=.8;
      this.limiter=this.context.createDynamicsCompressor();
      this.limiter.threshold.value=-1; this.limiter.knee.value=0; this.limiter.ratio.value=20;
      this.limiter.attack.value=.003; this.limiter.release.value=.1;
      this.master.connect(this.analyser); this.analyser.connect(this.limiter); this.limiter.connect(this.context.destination);
    }
    await this.context.resume();
    return this.context;
  }

  async prepare(project:Project) {
    const context=await this.init();
    const keep=new Set(project.tracks.map(t=>t.asset_id));
    for(const key of this.cache.keys()) if(!keep.has(key)) this.cache.delete(key);
    // Sequential decoding limits transient memory usage and avoids duplicate decodes.
    for(const track of project.tracks) {
      if(this.cache.has(track.asset_id)) continue;
      const response=await fetch(assetURL(project,track.asset_id));
      if(!response.ok) throw new Error('音频资产读取失败');
      const decoded=await context.decodeAudioData(await response.arrayBuffer());
      this.cache.set(track.asset_id,decoded);
    }
  }

  position():number {
    if(!this.context || !this.playing) return this.parked;
    return Math.min(this.endPosition,this.startPosition+Math.max(0,this.context.currentTime-this.startAt));
  }

  stop():number {
    const position=this.position();
    for(const playing of this.sources.values()) {
      try { playing.source.stop(); } catch { /* Already ended. */ }
      playing.source.disconnect(); playing.gain.disconnect(); playing.pan.disconnect();
    }
    this.sources.clear();
    for(const source of this.clickSources) { try { source.stop(); source.disconnect(); } catch { /* Ended. */ } }
    this.clickSources=[];
    this.playing=false; this.parked=position;
    return position;
  }

  private scheduleGain(track:Track, gain:GainNode, localTime:number, when:number, duration:number, project:Project) {
    const solo=project.tracks.some(t=>t.solo);
    gain.gain.cancelScheduledValues(when);
    if(track.muted || (solo&&!track.solo)) { gain.gain.setValueAtTime(0,when); return; }
    gain.gain.setValueAtTime(gainAt(track,localTime),when);
    for(const point of track.automation) {
      if(point.time>localTime && point.time<=localTime+duration)
        gain.gain.exponentialRampToValueAtTime(10**((track.gain_db+point.db)/20),when+point.time-localTime);
    }
  }

  async play(project:Project, from=0, end=project.duration, countIn=0, leadIn=.1):Promise<number> {
    if(this.recording) throw new Error('请先停止录音');
    await this.prepare(project);
    this.stop();
    const ctx=this.context!;
    const when=ctx.currentTime+leadIn+countIn*60/project.bpm;
    this.startAt=when; this.startPosition=from; this.endPosition=end; this.parked=from;
    for(const track of project.tracks) {
      const buffer=this.cache.get(track.asset_id)!;
      const delay=Math.max(0,track.offset-from);
      const local=Math.max(0,from-track.offset);
      const duration=Math.min(buffer.duration-local,end-from-delay);
      if(duration<=0) continue;
      const source=ctx.createBufferSource(); source.buffer=buffer;
      const gain=ctx.createGain(); const pan=ctx.createStereoPanner(); pan.pan.value=track.pan;
      this.scheduleGain(track,gain,local,when+delay,duration,project);
      source.connect(pan); pan.connect(gain); gain.connect(this.master!);
      source.start(when+delay,local,duration);
      this.sources.set(track.id,{source,gain,pan,offset:track.offset});
    }
    for(let i=0;i<countIn;i++) this.click(when-(countIn-i)*60/project.bpm,i===0);
    this.playing=true;
    return when;
  }

  refreshMix(project:Project) {
    if(!this.context || !this.playing) return;
    const position=this.position();
    for(const track of project.tracks) {
      const live=this.sources.get(track.id); if(!live) continue;
      live.pan.pan.setTargetAtTime(track.pan,this.context.currentTime,.01);
      const delay=Math.max(0,track.offset-position);
      this.scheduleGain(track,live.gain,Math.max(0,position-track.offset),this.context.currentTime+delay,Math.max(0,this.endPosition-position-delay),project);
    }
  }

  private click(when:number, accent:boolean) {
    const ctx=this.context!;
    const oscillator=ctx.createOscillator(); const gain=ctx.createGain();
    oscillator.frequency.value=accent?1500:1100;
    gain.gain.setValueAtTime(.07,when); gain.gain.exponentialRampToValueAtTime(.001,when+.04);
    oscillator.connect(gain); gain.connect(this.master!); oscillator.start(when); oscillator.stop(when+.045);
    this.clickSources.push(oscillator);
  }

  async connectMic(deviceId='') {
    if(this.recording || this.arming) throw new Error('录音期间不能更换输入设备');
    const ctx=await this.init();
    if(!navigator.mediaDevices?.getUserMedia) throw new Error('麦克风需要 localhost 或 HTTPS 安全页面');
    const stream=await navigator.mediaDevices.getUserMedia({audio:{
      ...(deviceId?{deviceId:{exact:deviceId}}:{}), channelCount:1,
      echoCancellation:false,noiseSuppression:false,autoGainControl:false,
    }});
    // Acquire first, then release the previous device. A failed switch keeps the old input.
    this.disconnectMic();
    this.stream=stream; this.microphoneName=stream.getAudioTracks()[0]?.label || '麦克风';
    this.micSource=ctx.createMediaStreamSource(stream);
    this.micAnalyser=ctx.createAnalyser(); this.micAnalyser.fftSize=2048;
    this.monitor=ctx.createGain(); this.monitor.gain.value=0;
    await ctx.audioWorklet.addModule('/recorder-worklet.js');
    this.recorder=new AudioWorkletNode(ctx,'pcm-recorder',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1],channelCount:1,channelCountMode:'explicit'});
    this.micSource.connect(this.micAnalyser); this.micSource.connect(this.recorder);
    this.micSource.connect(this.monitor); this.monitor.connect(this.master!);
    this.recorder.connect(ctx.destination);
    this.recorder.port.onmessage=({data}:{data:{type:string;samples?:Float32Array;total:number}})=>{
      if(data.type==='data' && data.samples) { this.chunks.push(data.samples); this.capturedSeconds=data.total/ctx.sampleRate; }
      if(data.type==='done') {
        const capture={blob:encodeWav(this.chunks,data.total,ctx.sampleRate),duration:data.total/ctx.sampleRate,sampleRate:ctx.sampleRate};
        this.chunks=[]; this.recording=false; const finish=this.finishCapture; this.finishCapture=null; finish?.(capture);
      }
    };
    for(const track of stream.getTracks()) track.onended=()=>{ this.stopRecording(); this.micReady=false; this.onDeviceLost?.(); };
    this.micReady=true;
  }

  disconnectMic() {
    this.stream?.getTracks().forEach(t=>{ t.onended=null; t.stop(); });
    this.micSource?.disconnect(); this.micAnalyser?.disconnect(); this.monitor?.disconnect(); this.recorder?.disconnect();
    this.stream=null; this.micSource=null; this.micAnalyser=null; this.monitor=null; this.recorder=null; this.micReady=false; this.monitoring=false;
  }

  monitorMic(enabled:boolean) { this.monitoring=enabled; if(this.monitor&&this.context) this.monitor.gain.setTargetAtTime(enabled ? .7 : 0,this.context.currentTime,.03); }
  setVolume(value:number) { this.volume=value; if(this.master&&this.context) this.master.gain.setTargetAtTime(value,this.context.currentTime,.02); }

  async setOutput(deviceId:string) {
    const ctx=await this.init();
    const withSink=ctx as AudioContext & {setSinkId?:(id:string)=>Promise<void>};
    if(!withSink.setSinkId) throw new Error('当前浏览器不支持 AudioContext 输出选择。请在系统声音设置中切换设备。');
    await withSink.setSinkId(deviceId);
  }

  async record(project:Project, start:number, end:number, latencyMs:number, countIn:number):Promise<Capture> {
    if(this.recording || this.arming) throw new Error('录音已经开始');
    if(!this.micReady) await this.connectMic();
    this.arming=true; this.captureCancelled=false;
    let when:number;
    try { when=await this.play(project,start,end,countIn,.1+Math.max(0,-latencyMs/1000)); }
    finally { this.arming=false; }
    if(this.captureCancelled) { this.stop(); return {blob:encodeWav([],0,this.sampleRate),duration:0,sampleRate:this.sampleRate}; }
    this.recording=true; this.chunks=[]; this.capturedSeconds=0;
    const ctx=this.context!;
    return new Promise(resolve=>{
      this.finishCapture=resolve;
      this.recorder!.port.postMessage({type:'arm',startFrame:Math.round((when+latencyMs/1000)*ctx.sampleRate),endFrame:Math.round((when+latencyMs/1000+end-start)*ctx.sampleRate)});
    });
  }

  stopRecording() { if(this.arming)this.captureCancelled=true; if(this.recording) this.recorder?.port.postMessage({type:'stop'}); }
  get sampleRate() { return this.context?.sampleRate ?? 48000; }
  get outputLatency() { return ((this.context?.baseLatency??0)+(this.context?.outputLatency??0))*1000; }
  get hasSinkSelection() { return !!(this.context && 'setSinkId' in this.context); }
  get micSettings() { return this.stream?.getAudioTracks()[0]?.getSettings(); }
  close() { this.stopRecording(); this.stop(); this.disconnectMic(); void this.context?.close(); }
}
