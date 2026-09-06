import type { Asset, Track, Note, Pitch } from './types.js';
import { clock, noteName } from './api.js';

export function canvasContext(canvas:HTMLCanvasElement):CanvasRenderingContext2D {
  const ratio=Math.min(window.devicePixelRatio || 1,2);
  const {width,height}=canvas.getBoundingClientRect();
  if(canvas.width!==Math.round(width*ratio) || canvas.height!==Math.round(height*ratio)) {
    canvas.width=Math.round(width*ratio); canvas.height=Math.round(height*ratio);
  }
  const ctx=canvas.getContext('2d')!;
  ctx.setTransform(ratio,0,0,ratio,0,0);
  return ctx;
}

export class PixelTide {
  private frame=0;
  private frequency=new Uint8Array(1024);
  private pointer=.5;
  private reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  constructor(private canvas:HTMLCanvasElement) {
    canvas.addEventListener('pointermove',e=>{ this.pointer=(e.clientX-canvas.getBoundingClientRect().left)/canvas.clientWidth; });
  }
  draw(analyser:AnalyserNode|null, running:boolean) {
    const ctx=canvasContext(this.canvas); const w=this.canvas.clientWidth,h=this.canvas.clientHeight;
    ctx.clearRect(0,0,w,h);
    if(!this.reduced) this.frame++;
    if(analyser&&running) analyser.getByteFrequencyData(this.frequency);
    const energy=running?this.frequency.slice(2,70).reduce((a,b)=>a+b,0)/68/255:0;
    const time=this.frame*.012;
    const columns=Math.ceil(w/8),rows=30;
    for(let z=rows-1;z>=0;z--) {
      const depth=z/(rows-1);
      for(let x=0;x<columns;x++) {
        const u=x/columns;
        const bass=running?(this.frequency[Math.floor(u*100)+1]??0)/255:0;
        const wave=Math.sin(u*12.5+depth*7.8-time*1.3)*Math.cos(u*4.8-depth*5+time*.6);
        const secondary=Math.sin(u*23+depth*8-time*1.8)*.24;
        const amplitude=(15+depth*30)*(1+energy*.6);
        const y=h*.30+depth*h*.53+(wave+secondary)*amplitude-bass*16*(1-depth*.5);
        const px=u*w+(depth-.5)*6;
        const size=depth>.6?3.1:2.2;
        const light=Math.floor(75+depth*110+Math.max(0,wave)*60);
        ctx.fillStyle=`rgba(${light},${light},${light},${.35+depth*.55})`;
        ctx.fillRect(Math.round(px),Math.round(y),size,size);
      }
    }
    // A restrained monochrome halo follows the pointer; never a fake audio meter.
    if(!this.reduced) {
      ctx.strokeStyle='rgba(255,255,255,.08)'; ctx.beginPath();
      ctx.arc(this.pointer*w,h*.6,24+energy*40,0,Math.PI*2); ctx.stroke();
    }
  }
}

export function waveform(canvas:HTMLCanvasElement, asset:Asset, track:Track, duration:number, zoom:number, selection:[number,number]|null, selected:boolean, position:number) {
  const ctx=canvasContext(canvas); const w=canvas.clientWidth,h=canvas.clientHeight;
  ctx.clearRect(0,0,w,h);
  const scale=w/duration;
  for(let t=0;t<=duration;t+=duration>120?10:duration>30?5:1) {
    const x=t*scale; ctx.strokeStyle='rgba(255,255,255,.045)'; ctx.beginPath(); ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();
  }
  ctx.strokeStyle='rgba(255,255,255,.07)';ctx.beginPath();ctx.moveTo(0,h/2);ctx.lineTo(w,h/2);ctx.stroke();
  const left=track.offset*scale,right=(track.offset+asset.duration)*scale;
  ctx.fillStyle=selected?'rgba(255,255,255,.035)':'rgba(255,255,255,.015)';ctx.fillRect(left,8,right-left,h-16);
  const gain=10**(track.gain_db/20);
  ctx.fillStyle=track.muted?'#484848':selected?'#e0e0e0':'#959595';
  const bar=2,step=3;
  for(let x=left;x<Math.min(w,right);x+=step) {
    const start=Math.floor((x-left)/(right-left)*asset.peaks.length);
    const end=Math.min(asset.peaks.length,Math.max(start+1,Math.ceil((x+step-left)/(right-left)*asset.peaks.length)));
    let low=0,high=0;
    for(let i=start;i<end;i++) { low=Math.min(low,asset.peaks[i]?.[0]??0); high=Math.max(high,asset.peaks[i]?.[1]??0); }
    const y1=h/2-Math.min(.92,high*gain*1.5)*(h*.41),y2=h/2-Math.max(-.92,low*gain*1.5)*(h*.41);
    ctx.fillRect(Math.round(x),y1,bar,Math.max(1,y2-y1));
  }
  if(track.automation.length) {
    ctx.strokeStyle='#ffffff';ctx.lineWidth=1;ctx.setLineDash([3,3]);ctx.beginPath();
    for(let i=0;i<track.automation.length;i++) {
      const p=track.automation[i]!;const x=(p.time+track.offset)*scale,y=8+(12-p.db)/72*(h-16);
      if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
      ctx.fillStyle='#fff';ctx.fillRect(x-2,y-2,4,4);
    }
    ctx.stroke();ctx.setLineDash([]);
  }
  if(selection) {
    const x=selection[0]*scale,b=selection[1]*scale;
    ctx.fillStyle='rgba(255,255,255,.09)';ctx.fillRect(x,0,b-x,h);
    ctx.strokeStyle='rgba(255,255,255,.4)';ctx.strokeRect(x+.5,.5,Math.max(0,b-x-1),h-1);
  }
  if(position>=0 && position<=duration) {
    ctx.fillStyle='#fff';ctx.fillRect(Math.round(position*scale),0,1,h);
  }
  void zoom;
}

export function ruler(canvas:HTMLCanvasElement,duration:number,position:number) {
  const ctx=canvasContext(canvas),w=canvas.clientWidth,h=canvas.clientHeight;
  ctx.clearRect(0,0,w,h);ctx.font='10px ui-monospace, monospace';
  const minStep=duration*58/Math.max(1,w);
  const step=[1,2,5,10,15,30,60,120,300,600,1200].find(n=>n>=minStep)??1200;
  for(let s=0;s<=duration;s+=step) {
    const x=s/duration*w;ctx.fillStyle='#666';ctx.fillRect(Math.round(x),h-7,1,7);ctx.fillText(clock(s),x+5,12);
  }
  ctx.fillStyle='#eee';const x=position/duration*w;ctx.beginPath();ctx.moveTo(x-4,0);ctx.lineTo(x+4,0);ctx.lineTo(x,6);ctx.fill();
}

export function pitchRoll(canvas:HTMLCanvasElement,pitch:Pitch,notes:Note[],selected:number,duration:number):{min:number;max:number} {
  const ctx=canvasContext(canvas),w=canvas.clientWidth,h=canvas.clientHeight;
  const voiced=pitch.midi.filter((v):v is number=>v!==null);
  const min=Math.floor(Math.min(48,...voiced)-2),max=Math.ceil(Math.max(76,...voiced)+2);
  const y=(m:number)=>h-(m-min)/(max-min)*h;
  ctx.clearRect(0,0,w,h);ctx.font='9px ui-monospace, monospace';
  for(let m=min;m<=max;m++) {
    ctx.fillStyle=[1,3,6,8,10].includes(m%12)?'#121212':'#171717';
    ctx.fillRect(0,y(m+.5),w,h/(max-min));
    if(m%12===0) {ctx.fillStyle='#777';ctx.fillText(noteName(m),3,y(m)-2);}
  }
  for(let i=0;i<notes.length;i++) {
    const n=notes[i]!;
    ctx.fillStyle=i===selected?'#eeeeee':'#404040';
    ctx.fillRect(n.start/duration*w,y(n.midi+.35),Math.max(2,(n.end-n.start)/duration*w),Math.max(3,h/(max-min)*.7));
  }
  const line=(values:(number|null)[],style:string)=>{
    ctx.strokeStyle=style;ctx.lineWidth=1.2;ctx.beginPath();let prev=false;
    values.forEach((m,i)=>{
      if(m===null){prev=false;return;}
      const x=(pitch.times[i]??0)/duration*w;
      if(prev)ctx.lineTo(x,y(m));else ctx.moveTo(x,y(m));prev=true;
    });ctx.stroke();
  };
  line(pitch.midi,'#a4a4a4');if(pitch.target)line(pitch.target,'#ffffff');
  return {min,max};
}

export function eqCurve(canvas:HTMLCanvasElement,low:number,mid:number,high:number) {
  const ctx=canvasContext(canvas),w=canvas.clientWidth,h=canvas.clientHeight;
  ctx.clearRect(0,0,w,h);ctx.strokeStyle='#252525';
  for(let i=1;i<4;i++){ctx.beginPath();ctx.moveTo(0,h*i/4);ctx.lineTo(w,h*i/4);ctx.stroke();}
  ctx.strokeStyle='#ededed';ctx.beginPath();
  for(let x=0;x<w;x++) {
    const f=20*10**(x/w*3);
    // Visual response approximation; exact biquad filtering occurs in FFmpeg.
    const v=low/(1+(f/160)**2)+mid*Math.exp(-(Math.log(f/1600)**2)/1.3)+high/(1+(6500/f)**2);
    const y=h/2-v*(h/30);
    if(x===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();
}
