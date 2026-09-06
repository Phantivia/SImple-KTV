import { api, upload, esc, clock, noteName, assetURL } from './api.js';
import { AudioEngine } from './engine.js';
import type { Capture } from './engine.js';
import type { Project, Track, Job, Health, Model, Note } from './types.js';
import { activeJob } from './types.js';
import { PixelTide, waveform, ruler, pitchRoll, eqCurve } from './visuals.js';

const $=<T extends HTMLElement=HTMLElement>(id:string):T=>{ const element=document.getElementById(id); if(!element)throw new Error(`Missing UI element: ${id}`); return element as T; };
const engine=new AudioEngine();
const tide=new PixelTide($<HTMLCanvasElement>('pixel-tide'));
const stageTide=new PixelTide($<HTMLCanvasElement>('stage-tide'));
type Tab='process'|'pitch'|'mix'|'devices';
const presets:Record<string,Record<string,number>>={
  natural:{denoise:.18,highpass:80,low_db:-1,mid_db:1,high_db:1.5,compressor_db:-20,ratio:3,deesser:.2,reverb:.12,delay:0,output_db:0},
  clear:{denoise:.25,highpass:100,low_db:-2,mid_db:2,high_db:2,compressor_db:-22,ratio:3.5,deesser:.3,reverb:.07,delay:0,output_db:1},
  dream:{denoise:.15,highpass:90,low_db:0,mid_db:-1,high_db:1,compressor_db:-22,ratio:2.5,deesser:.15,reverb:.3,delay:.13,output_db:0},
  dry:{denoise:0,highpass:0,low_db:0,mid_db:0,high_db:0,compressor_db:0,ratio:1,deesser:0,reverb:0,delay:0,output_db:0}
};
const state={
  project:null as Project|null,selected:'',tab:'process' as Tab,health:null as Health|null,models:[] as Model[],
  projects:[] as {id:string;name:string;duration:number;updated_at:string}[],jobs:[] as Job[],
  busy:false,capturing:false,uploadProgress:null as number|null,uploadLabel:'',
  zoom:1,selection:null as [number,number]|null,loop:false,key:0,
  fx:{...presets.natural!},preset:'natural',model:'bs-roformer',quality:'balanced',
  pitchEngine:'pyin',strength:.75,retune:100,tonic:0,scale:'major',
  notes:[] as Note[],edited:new Set<number>(),noteSelected:-1,pitchTrack:'',
  deviceIn:'',deviceOut:'',latency:0,countIn:4,voices:[2,4] as number[],
  stage:false,unsaved:null as {capture:Capture;projectId:string;offset:number}|null,
};
let toastTimer=0;
let pollRunning=false;
let loopPending=false;
let lastHealth=0;
let inputLevel=-100;
let livePeak=-100;
let lastCanvasRender=0;
let lastMeterRender=0;
let pitchBounds={min:48,max:80};
let latestJob:Job|null=null;
let pendingCaptureURL='';

function toast(text:string,error=false) {
  const box=$('toast');box.textContent=text;box.classList.remove('hidden');box.classList.toggle('error',error);
  window.clearTimeout(toastTimer);toastTimer=window.setTimeout(()=>box.classList.add('hidden'),error?14000:5500);
}
function fail(error:unknown) {
  const text=error instanceof Error?error.message:String(error);toast(text,true);
  if(text.includes('(409)')&&state.project) void api<Project>(`/projects/${state.project.id}`).then(p=>setProject(p)).catch(()=>undefined);
}
function current():Project { if(!state.project)throw new Error('先导入原版歌曲，或打开合成演示。');return state.project; }
function selected():Track { const p=current();const t=p.tracks.find(t=>t.id===state.selected);if(!t)throw new Error('先选择要处理的音轨。');return t; }
function locked() { return state.busy||state.capturing||state.uploadProgress!==null||state.jobs.some(activeJob); }
function writable() { if(state.unsaved)throw new Error('还有未保存的录音，请先重试保存或下载并确认另存。'); if(locked())throw new Error('当前有录音或处理任务。请先停止录音，或取消 / 完成当前任务。'); }
function setProject(p:Project,selectLast=false) {
  const changed=state.project?.id!==p.id;
  if(changed){engine.stop();engine.parked=0;state.selection=null;state.pitchTrack='';}
  state.project=p;state.key=p.key_shift;state.tonic=p.tonic;state.scale=p.scale;
  if(changed||selectLast||!p.tracks.some(t=>t.id===state.selected)) state.selected=p.tracks.filter(t=>!t.muted).at(-1)?.id||p.tracks[0]?.id||'';
  try{localStorage.setItem('simple-ktv-project',p.id);}catch{/* Private browsing may disallow storage. */}
  $('project-name').textContent=p.name;$('session-number').textContent=p.id.slice(0,3).toUpperCase();
  $('total-time').textContent=clock(p.duration,true);$('track-count').textContent=`${String(p.tracks.length).padStart(2,'0')} TRACKS`;
  $('save-status').textContent=`已保存 / REV ${String(p.revision).padStart(3,'0')}`;
  $('hero-status').textContent=`◌  ${p.sample_rate.toLocaleString()} Hz / ${p.tracks.length} TRACKS / LOCAL`;
  $('key-value').textContent=p.key_shift>0?`+${p.key_shift}`:String(p.key_shift);
  engine.refreshMix(p);renderTimeline();renderInspector();renderButtons();
}
function renderButtons() {
  const has=!!state.project, lock=locked();
  $<HTMLButtonElement>('undo-button').disabled=!has||!state.project?.can_undo||lock;
  $<HTMLButtonElement>('redo-button').disabled=!has||!state.project?.can_redo||lock;
  $<HTMLButtonElement>('play-button').disabled=!has||state.busy||state.capturing||state.uploadProgress!==null;
  $<HTMLButtonElement>('record-button').disabled=!has||state.busy||state.uploadProgress!==null||state.jobs.some(activeJob);
  $('record-button').classList.toggle('armed',state.capturing);
  $('record-button').querySelector('span')!.textContent=state.capturing?'停止录制':'录制';
  $('loop-button').classList.toggle('enabled',state.loop);
  for(const e of document.querySelectorAll<HTMLButtonElement>('[data-write]')){
    const needsSeparator=['separate','ai-denoise','ai-dereverb'].includes(e.dataset.action||'');
    e.disabled=lock||!has||(e.dataset.track==='1'&&!state.selected)||(needsSeparator&&!state.health?.separation);
    if(needsSeparator&&!state.health?.separation)e.title='此环境未安装神经模型依赖，请使用 GPU 镜像。';
  }
}
function renderTimeline() {
  const p=state.project;
  $('empty-state').classList.toggle('hidden',!!p?.tracks.length);
  $('timeline-scroll').classList.toggle('hidden',!p?.tracks.length);
  $('selection-bar').classList.toggle('hidden',!p?.tracks.length);
  if(!p){$('track-list').innerHTML='';return;}
  const roles:Record<string,string>={original:'ORIGINAL',accompaniment:'BACKING',reference:'REFERENCE',vocal:'VOCAL TAKE',processed:'PROCESSED',harmony:'HARMONY'};
  $('track-list').innerHTML=p.tracks.map((t,i)=>`<div class="track-row ${t.id===state.selected?'active':''}" data-track-row="${t.id}"><div class="track-head" data-action="select-track" data-id="${t.id}"><div class="track-topline"><span class="track-no">${String(i+1).padStart(2,'0')}</span><span>${roles[t.role]||'AUDIO'}</span></div><h3 class="track-name" title="${esc(t.name)}">${esc(t.name)}</h3><div class="track-controls"><button class="track-toggle ${t.muted?'on':''}" data-action="mute" data-id="${t.id}" title="静音 ${esc(t.name)}" aria-label="静音 ${esc(t.name)}">M</button><button class="track-toggle ${t.solo?'on':''}" data-action="solo" data-id="${t.id}" title="独奏 ${esc(t.name)}" aria-label="独奏 ${esc(t.name)}">S</button><span class="track-gain">${t.gain_db>0?'+':''}${t.gain_db.toFixed(1)} dB</span></div></div><canvas class="track-canvas" data-track="${t.id}" aria-label="${esc(t.name)} 波形。拖动选区，Shift 单击添加音量自动化。"></canvas></div>`).join('');
  $('timeline-inner').style.width=`${state.zoom*100}%`;
  for(const canvas of document.querySelectorAll<HTMLCanvasElement>('.track-canvas'))bindWaveform(canvas);
  drawTimeline();selectionText();
}
function selectionText() {
  $('selection-text').textContent=state.selection?`${clock(state.selection[0],true)} — ${clock(state.selection[1],true)} / ${(state.selection[1]-state.selection[0]).toFixed(2)} s`:'拖动选区 · Shift 单击添加音量曲线';
}
function drawTimeline() {
  const p=state.project;if(!p?.duration)return;
  const pos=engine.position();ruler($<HTMLCanvasElement>('ruler'),p.duration,pos);
  for(const canvas of document.querySelectorAll<HTMLCanvasElement>('.track-canvas')){
    const t=p.tracks.find(t=>t.id===canvas.dataset.track);if(!t)continue;
    const a=p.assets[t.asset_id];if(a)waveform(canvas,a,t,p.duration,state.zoom,state.selection,t.id===state.selected,pos);
  }
}
function bindWaveform(canvas:HTMLCanvasElement) {
  let start=0,initialX=0,dragged=false;
  const at=(e:PointerEvent)=>Math.min(current().duration,Math.max(0,(e.clientX-canvas.getBoundingClientRect().left)/canvas.clientWidth*current().duration));
  canvas.addEventListener('pointerdown',e=>{
    if(state.capturing||state.busy)return;
    if(e.button!==0)return;
    state.selected=canvas.dataset.track!;highlightTrack();renderInspector();
    if(e.shiftKey){
      try{writable();const t=selected();const local=at(e)-t.offset;if(local<0||local>t.duration)return;
        const db=Math.round(12-(e.clientY-canvas.getBoundingClientRect().top)/canvas.clientHeight*72);
        const points=[...t.automation.filter(p=>Math.abs(p.time-local)>.03),{time:Math.round(local*1000)/1000,db:Math.max(-60,Math.min(12,db))}].sort((a,b)=>a.time-b.time);
        void patchTrack(t.id,{automation:points}).catch(fail);
      }catch(error){fail(error);}return;
    }
    engine.stop();start=at(e);initialX=e.clientX;dragged=false;canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove',e=>{
    if(!canvas.hasPointerCapture(e.pointerId))return;
    if(Math.abs(e.clientX-initialX)>4)dragged=true;
    if(dragged){const end=at(e);state.selection=[Math.min(start,end),Math.max(start,end)];selectionText();drawTimeline();}
  });
  canvas.addEventListener('pointerup',e=>{
    if(!canvas.hasPointerCapture(e.pointerId))return;canvas.releasePointerCapture(e.pointerId);
    if(!dragged){engine.parked=at(e);state.selection=null;}
    else engine.parked=state.selection?.[0]??start;
    selectionText();drawTimeline();
  });
}
function highlightTrack(){for(const row of document.querySelectorAll<HTMLElement>('[data-track-row]'))row.classList.toggle('active',row.dataset.trackRow===state.selected);}
function range(id:string,label:string,value:number,min:number,max:number,step:number,unit='') {
  return `<div class="control"><label class="control-label" for="${id}"><span>${label}</span><output id="${id}-value">${value}${unit}</output></label><input id="${id}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" data-unit="${unit}"/></div>`;
}
function toolButton(action:string,label:string,primary=true,track=true){return `<button class="${primary?'primary':'outline'} full" data-action="${action}" data-write ${track?'data-track="1"':''}>${label}</button>`;}
function renderInspector() {
  const p=state.project,t=p?.tracks.find(t=>t.id===state.selected);
  document.querySelectorAll<HTMLElement>('[data-tab]').forEach(e=>e.classList.toggle('active',e.dataset.tab===state.tab));
  const selectedHeader=`<div class="panel-kicker"><span class="eyebrow">${t?'SELECTED TRACK':'YOUR SIGNAL CHAIN'}</span><span class="mini-tag">${t?esc(t.role.toUpperCase()):'48 kHz'}</span></div><h3 class="panel-title">${t?esc(t.name):'先从一首歌开始。'}</h3>`;
  let content='';
  if(state.tab==='process'){
    const f=state.fx;
    content=`<section class="panel-block"><div class="panel-kicker"><span class="eyebrow">01 / SOURCE SEPARATION</span><span class="mini-tag">LOCAL AI</span></div><h3 class="panel-title">留下音乐，让你开口。</h3><div class="select-row"><select id="separation-model" aria-label="分离模型"><option value="bs-roformer">BS-RoFormer · ViperX</option><option value="melband">MelBand RoFormer · Kim</option><option value="ensemble">双模型顺序 Ensemble</option></select><select id="separation-quality" aria-label="分离质量"><option value="balanced">均衡</option><option value="quality">高质量</option></select></div>${toolButton('separate','分离原唱与伴奏 ↗',true,false)}<p class="panel-description">${state.health?.separation?'首次使用自动下载权重，后续使用本地缓存。RTX 笔记本默认 batch 1。':'当前环境未安装 AI 分离依赖；请使用 GPU Docker 镜像。录音与基础精修仍可使用。'}</p></section>
    <section class="panel-block">${selectedHeader}<div class="panel-kicker"><span class="eyebrow">02 / VOCAL POLISH</span><select class="preset-select" id="fx-preset" aria-label="精修预设"><option value="natural">自然 · Natural</option><option value="clear">清晰 · Clear</option><option value="dream">空间 · Dream</option><option value="dry">干声 · Dry</option></select></div>${range('fx-denoise','宽带降噪 · DSP',f.denoise!,0,1,.01)}<canvas id="eq-curve" class="eq-canvas" aria-label="均衡器响应示意"></canvas><div class="eq-labels"><span>20 Hz</span><span>1 kHz</span><span>20 kHz</span></div><div class="eq-controls">${range('fx-low_db','低频',f.low_db!,-12,12,.5,' dB')}${range('fx-mid_db','中频',f.mid_db!,-12,12,.5,' dB')}${range('fx-high_db','高频',f.high_db!,-12,12,.5,' dB')}</div>${range('fx-compressor_db','压缩阈值',f.compressor_db!,-60,0,1,' dB')}${range('fx-reverb','空间混响',f.reverb!,0,.6,.01)}<details class="advanced"><summary>展开完整效果链</summary>${range('fx-highpass','高通截止',f.highpass!,0,500,10,' Hz')}${range('fx-ratio','压缩比',f.ratio!,1,12,.5,':1')}${range('fx-deesser','齿音抑制',f.deesser!,0,1,.05)}${range('fx-delay','节拍同步 Delay',f.delay!,0,.6,.01)}${range('fx-output_db','补偿增益',f.output_db!,-24,12,.5,' dB')}<p class="panel-description">效果在离线渲染后生效；混响尾音保留 2 秒。均衡曲线为响应示意。</p></details>${toolButton('effects','渲染精修版本 ↗')}<p class="panel-description">新建精修轨，静音原 take。可随时 A/B 或撤销。</p></section><details class="advanced"><summary>AI 去噪 / 去混响</summary><p class="panel-description">RoFormer 修复模型是实验选项，可能改变唱腔或高频。会保留原轨，不会覆盖录音。</p><div class="button-row">${toolButton('ai-denoise','AI 去噪',false)}${toolButton('ai-dereverb','AI 去混响',false)}</div></details>`;
  }else if(state.tab==='pitch'){
    if(t?.id!==state.pitchTrack){state.pitchTrack=t?.id||'';state.notes=t?.pitch?.notes.map(n=>({...n}))||[];state.edited.clear();state.noteSelected=-1;}
    content=`<section class="panel-block">${selectedHeader}<div class="panel-note">用于干净的单声部人声。自动修音 = 音高分析 + 音阶目标 + PSOLA 重合成，不会假装使用生成式人声模型。</div><label class="field"><span>音高分析引擎</span><select id="pitch-engine"><option value="pyin">pYIN · CPU 算法</option><option value="torchcrepe" ${state.health?.torchcrepe?'':'disabled'}>TorchCREPE Full · 神经音高</option></select></label>${toolButton('analyze','分析音高曲线 ↗')}${t?.pitch?'<canvas id="pitch-roll" class="pitch-canvas" aria-label="音高曲线与可编辑音符。上下拖动音符修改目标音高。"></canvas><div class="notes-detail"><span id="note-label">单击或上下拖动音符</span><div><button class="small-step" data-action="note-down" aria-label="降低目标音符">−</button><button class="small-step" data-action="note-up" aria-label="提高目标音符">＋</button></div></div><p class="panel-description">灰线：分析音高；白线：上次渲染的目标。音符块可编辑，并非实时音频处理。</p>':'<div class="placeholder-plot">音高分析后，这里会显示真实音高曲线。</div>'}<div class="pitch-settings"><select id="tonic" aria-label="音阶主音">${['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'].map((n,i)=>`<option value="${i}">${n}</option>`).join('')}</select><select id="scale" aria-label="音阶"><option value="major">大调</option><option value="minor">自然小调</option><option value="chromatic">半音阶</option></select></div><p class="panel-description">请确认歌曲调性；此处默认值不是自动识别结果。</p>${range('pitch-strength','修正强度',state.strength,0,1,.05)}${range('pitch-retune','过渡平滑',state.retune,0,500,10,' ms')}${toolButton('tune','渲染自动调音 ↗')}</section><section class="panel-block"><div class="panel-kicker"><span class="eyebrow">HARMONY LAYERS</span><span class="mini-tag">SCALE-AWARE</span></div><h3 class="panel-title">让一个声音，有了层次。</h3><div class="voices">${[[2,'上三度'],[4,'上五度'],[-2,'下三度'],[7,'上八度']].map(([v,n])=>`<label class="check-row"><input type="checkbox" data-voice="${v}" ${state.voices.includes(Number(v))?'checked':''}/> ${n}</label>`).join('')}</div>${toolButton('harmony','生成独立和声轨 ↗')}<p class="panel-description">神经 / 算法音高驱动的音阶和声 + PSOLA，保留你的音色与时序。不是生成式编曲，也不自动判断借用和弦或转调。</p></section>`;
  }else if(state.tab==='mix'){
    content=`<section class="panel-block">${selectedHeader}${t?`${range('mix-gain','轨道音量',t.gain_db,-60,12,.5,' dB')}${range('mix-pan','声像 · L / R',t.pan,-1,1,.05)}<label class="field"><span>片段起点 / 秒</span><div class="button-row"><input type="number" id="mix-offset" value="${t.offset.toFixed(3)}" step=".001" min="0" max="2400" aria-label="片段起点"/><button class="outline" data-action="offset">保存对齐</button></div></label><div class="button-row"><button class="outline" data-action="rename">重命名</button><a class="outline" href="${assetURL(p!,t.asset_id)}?download=true">下载此音轨 ↗</a></div>`:'<p class="empty-mini">选择一个音轨开始混音。</p>'}</section><section class="panel-block"><div class="panel-kicker"><span class="eyebrow">GAIN AUTOMATION</span><span class="mini-tag">NON-DESTRUCTIVE</span></div><p class="panel-description">Shift + 单击波形添加节点。横轴是时间，纵轴是相对增益；节点间按 dB 线性插值，试听与导出使用同一条曲线。</p><div class="automation-list">${t?.automation.length?t.automation.map((pt,i)=>`<div>${clock(pt.time,true)} / ${pt.db>0?'+':''}${pt.db.toFixed(1)} dB <button class="quiet" data-action="delete-point" data-index="${i}">×</button></div>`).join(''):'还没有自动化节点。'}</div>${toolButton('clear-automation','清空增益曲线',false)}</section><section class="panel-block"><div class="panel-kicker"><span class="eyebrow">AUTO BALANCE</span><span class="mini-tag">LUFS</span></div><p class="panel-description">测量各可听轨的综合响度，给出伴奏、人声与和声的初始音量平衡。主母带响度在导出时统一处理。</p>${toolButton('automix','自动平衡混音 ↗',true,false)}${range('monitor-volume','试听总音量 · 不影响导出',engine.volume,0,1,.02)}</section>${t?'<button class="quiet danger full" data-action="delete-track">从工程移除此轨（可撤销）</button>':''}`;
  }else{
    const h=state.health;
    content=`<section class="panel-block"><div class="panel-kicker"><span class="eyebrow">AUDIO DEVICES</span><span class="mini-tag">WEB AUDIO</span></div><h3 class="panel-title">好的声音，从输入开始。</h3><button class="primary full" data-action="connect-mic">${engine.micReady?'刷新麦克风与设备':'授权麦克风 / 获取设备'}</button><label class="field"><span>输入设备</span><select id="input-device"><option value="">系统默认麦克风</option></select></label><div class="mini-meter"><span id="input-meter"></span></div><label class="field"><span>输出设备</span><select id="output-device"><option value="">系统默认输出</option></select></label><p class="panel-description">${engine.context&&!engine.hasSinkSelection?'当前浏览器不支持输出设备选择，请在系统声音设置中切换。':'输出选择依赖浏览器的 AudioContext.setSinkId 支持。'}</p><label class="check-row"><input type="checkbox" id="mic-monitor" ${engine.monitoring?'checked':''}/> 耳机软件监听（默认关闭）</label><p class="panel-description">录音只捕获麦克风，不包含播放的伴奏。关闭浏览器降噪、回声消除与自动增益，实际是否生效以设备信息为准。</p></section><section class="panel-block"><div class="panel-kicker"><span class="eyebrow">TIMING & ALIGNMENT</span></div>${range('latency','录音对齐补偿 · 需实测',state.latency,0,500,1,' ms')}<div class="pitch-settings"><label class="field"><span>录制前预备拍</span><select id="count-in"><option value="4">4 拍</option><option value="0">无预备拍</option></select></label><label class="field"><span>BPM · 手动设置</span><input type="number" id="bpm" min="30" max="300" value="${p?.bpm||100}"/></label></div><p class="panel-description">输出延迟估计：${engine.outputLatency.toFixed(1)} ms。输入延迟未知；USB 麦克风和蓝牙设备需要录制节拍后手动校准。</p></section><section class="panel-block"><div class="panel-kicker"><span class="eyebrow">ENGINE DIAGNOSTICS</span><span class="mini-tag">${h?.device.toUpperCase()||'OFFLINE'}</span></div><div class="device-status">${h?.gpu?`${esc(h.gpu.name)}<br/>VRAM ${h.gpu.vram_gb} GiB · SM ${h.gpu.compute_capability}`:'CUDA GPU / 未检测到'}<br/>TORCH ${esc(h?.torch||'未安装')}<br/>CUDA ${esc(h?.cuda_runtime||'—')}<br/>AUDIO ${engine.sampleRate.toLocaleString()} Hz<br/>MIC ${esc(engine.microphoneName)}</div>${h?.gpu_error?`<p class="panel-note">${esc(h.gpu_error)}</p>`:''}<details class="advanced"><summary>查看麦克风实际约束</summary><pre class="detail-json">${esc(JSON.stringify(engine.micSettings||{status:'请先授权麦克风'},null,2))}</pre></details><p class="panel-description">${h?.max_upload_mb||200} MB / ${(h?.max_duration||1200)/60} 分钟单文件限制。默认仅监听 localhost，不上传音频至云端。</p></section>`;
  }
  $('inspector-body').innerHTML=content;
  const values:Record<string,string>={'fx-preset':state.preset,'separation-model':state.model,'separation-quality':state.quality,'pitch-engine':state.pitchEngine,'tonic':String(state.tonic),'scale':state.scale,'count-in':String(state.countIn)};
  for(const [id,value] of Object.entries(values)){const e=document.getElementById(id) as HTMLSelectElement|null;if(e)e.value=value;}
  if(state.tab==='process')drawEq();
  if(state.tab==='pitch'&&t?.pitch){drawPitch();bindPitch();}
  if(state.tab==='devices')void populateDevices().catch(()=>undefined);
  renderButtons();
}
function drawEq(){const canvas=document.getElementById('eq-curve') as HTMLCanvasElement|null;if(canvas)eqCurve(canvas,state.fx.low_db!,state.fx.mid_db!,state.fx.high_db!);}
function drawPitch(){const t=state.project?.tracks.find(t=>t.id===state.selected);const canvas=document.getElementById('pitch-roll') as HTMLCanvasElement|null;if(t?.pitch&&canvas)pitchBounds=pitchRoll(canvas,t.pitch,state.notes,state.noteSelected,t.duration);const label=document.getElementById('note-label');if(label){const note=state.notes[state.noteSelected];label.textContent=note?`${noteName(note.midi)} / ${clock(note.start,true)} · 已编辑 ${state.edited.size}`:'单击或上下拖动音符';}}
function bindPitch(){
  const canvas=$<HTMLCanvasElement>('pitch-roll');let startY=0,initial=0;
  canvas.addEventListener('pointerdown',e=>{if(locked())return;const t=selected();const time=(e.clientX-canvas.getBoundingClientRect().left)/canvas.clientWidth*t.duration;
    const index=state.notes.findIndex(n=>time>=n.start&&time<=n.end);if(index<0)return;
    state.noteSelected=index;startY=e.clientY;initial=state.notes[index]!.midi;canvas.setPointerCapture(e.pointerId);drawPitch();});
  canvas.addEventListener('pointermove',e=>{if(!canvas.hasPointerCapture(e.pointerId)||state.noteSelected<0)return;
    const delta=Math.round((startY-e.clientY)/canvas.clientHeight*(pitchBounds.max-pitchBounds.min));
    state.notes[state.noteSelected]!.midi=Math.max(24,Math.min(108,initial+delta));if(delta!==0)state.edited.add(state.noteSelected);drawPitch();});
  canvas.addEventListener('pointerup',e=>{if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);});
}
async function patchTrack(id:string,patch:Record<string,unknown>){writable();const p=current();const result=await api<Project>(`/projects/${p.id}/tracks/${id}`,'PATCH',{revision:p.revision,...patch});setProject(result);}
async function patchProject(patch:Record<string,unknown>){writable();const p=current();setProject(await api<Project>(`/projects/${p.id}`,'PATCH',{revision:p.revision,...patch}));}
async function runJob(task:string,options:Record<string,unknown>={}){
  writable();const p=current();engine.stop();
  state.busy=true;renderButtons();
  try{const j=await api<Job>(`/projects/${p.id}/jobs`,'POST',{revision:p.revision,task,...options});state.jobs=[j,...state.jobs];latestJob=j;renderJob();}
  finally{state.busy=false;renderButtons();}
}
function renderJob(){
  const banner=$('job-banner');
  if(state.uploadProgress!==null){banner.classList.remove('hidden');banner.innerHTML=`<div class="job-info"><div class="job-status">IMPORT / SAVE</div><div class="job-stage">${esc(state.uploadLabel)}</div></div><span class="mono">${Math.round(state.uploadProgress*100)}%</span><span class="job-progress" style="width:${state.uploadProgress*100}%"></span>`;return;}
  const job=state.jobs.find(activeJob)||latestJob;
  if(!job){banner.classList.add('hidden');return;}
  banner.classList.remove('hidden');
  const active=activeJob(job);
  banner.innerHTML=`<div class="job-info"><div class="job-status">${esc(job.task.toUpperCase())} / ${esc(job.state.toUpperCase())}</div><div class="job-stage">${esc(job.error||job.stage)}</div></div>${active?`<button class="quiet" data-action="cancel-job" data-id="${job.id}">取消 ×</button>`:job.result?.export?`<a class="outline" href="/api/jobs/${job.id}/download">下载作品 ↗</a>`:`<button class="quiet" data-action="dismiss-job">×</button>`}${job.state==='failed'?`<a class="quiet" href="/api/jobs/${job.id}/log">日志 ↗</a>`:''}<span class="job-progress ${active&&job.progress===null?'indeterminate':''}" style="width:${(job.progress||0)*100}%"></span>`;
}
async function refreshProject(last=false){if(state.project){const p=await api<Project>(`/projects/${state.project.id}`);setProject(p,last);}}
async function poll(){
  if(pollRunning||document.hidden)return;pollRunning=true;
  try{
    if(state.project){const id=state.project.id;const jobs=await api<Job[]>(`/projects/${id}/jobs`);if(state.project?.id!==id)return;
      for(const j of jobs){const old=state.jobs.find(o=>o.id===j.id);if(old&&activeJob(old)&&!activeJob(j)){
        latestJob=j;
        if(j.state==='completed'){if(j.task==='pitch')state.pitchTrack='';await refreshProject(j.task!=='pitch'&&j.task!=='transpose'&&j.task!=='erase'&&j.task!=='automix');if(j.result?.export)showExportResult(j);else toast('处理完成，结果已保存。');}
        else if(j.state==='failed')toast(j.error||'任务失败，原音频保留。',true);
        else toast('任务已取消，原音频保留。');
      }}state.jobs=jobs;renderJob();renderButtons();
    }
    if(performance.now()-lastHealth>30000){state.health=await api<Health>('/health');lastHealth=performance.now();renderHealth();}
  }catch{/* Keep the last visible state; actions report network errors explicitly. */}
  finally{pollRunning=false;}
}
function renderHealth(){const h=state.health;$('runtime-badge').innerHTML=`<i></i>${h?.device==='cuda'?'CUDA / LOCAL GPU':'LOCAL / CPU ENGINE'}`;}
function modal(title:string,subtitle:string,body:string){$('modal-title').textContent=title;$('modal-subtitle').textContent=subtitle;$('modal-body').innerHTML=body;const dialog=$<HTMLDialogElement>('modal');if(!dialog.open)dialog.showModal();}
function closeModal(){$<HTMLDialogElement>('modal').close();}
async function library(){if(state.capturing)throw new Error('请先停止录音');state.projects=await api<typeof state.projects>('/projects');modal('你的声音档案。','PROJECT LIBRARY',`${state.projects.length?state.projects.map(p=>`<button class="library-row" data-action="open-project" data-id="${p.id}"><div><strong>${esc(p.name)}</strong><span>${clock(p.duration)} · ${esc(p.updated_at.slice(0,10))}</span></div><span>↗</span></button>`).join(''):'<p>工程库还是空的。导入歌曲，或打开合成演示。</p>'}<div class="button-row"><button class="primary full" data-action="import">导入新歌曲</button><button class="outline full" data-action="demo">合成演示 ↗</button></div>`);}
async function modelLibrary(){state.models=await api<Model[]>('/models');modal('模型，不藏在黑盒里。','LOCAL MODEL REGISTRY',`<p>模型在任务首次运行时下载到本地模型卷。以下为固定允许列表；不接受任意上传的 pickle 权重。模型架构先进不等于在所有歌曲上效果最佳，请 A/B 试听。</p>${state.models.map(m=>`<div class="modal-card"><span class="eyebrow">${m.downloaded?'CACHED / 本地已缓存':'DOWNLOAD ON FIRST USE'}</span><h4>${esc(m.name)}</h4><p class="mono">${esc(m.filename)}</p></div>`).join('')}<p>修音可选择 TorchCREPE Full 神经音高分析。和声目前是音阶驱动的 PSOLA 分层，并不是生成式四声部编曲模型。代码许可与各权重许可分开，商用前需核实权重授权。</p>`);}
function history(){modal('每一次处理，都有记录。','JOB HISTORY',state.jobs.length?state.jobs.map(j=>`<div class="modal-card"><span class="eyebrow">${esc(j.state.toUpperCase())}</span><h4>${esc(j.task)} / ${esc(j.stage)}</h4>${j.error?`<p>${esc(j.error)}</p>`:''}<div class="button-row">${j.result?.export?`<a class="outline" href="/api/jobs/${j.id}/download">下载作品 ↗</a>`:''}<a class="quiet" href="/api/jobs/${j.id}/log">处理日志</a>${activeJob(j)?`<button class="quiet" data-action="cancel-job" data-id="${j.id}">取消任务</button>`:''}</div></div>`).join(''):'<p>还没有处理任务。</p>');}
function help(){modal('从原曲，到你的版本。','SIMPLE KTV / FIELD GUIDE',`<h3>01 / 导入与分离</h3><p>导入原版 MP3，在“处理”中选择 RoFormer 分离。原唱参考默认静音，伴奏默认打开。KEY 调整后点击“应用”进行离线保速变调；变调会从各轨未变调的源资产重新渲染，避免反复降质。</p><h3>02 / 录制与补录</h3><p>在“设备”授权麦克风并戴上耳机。R 开始录制，Space 播放。预备拍与录音使用同一个 AudioContext 时钟。拖动波形选择片段，再“分段补录”可覆盖该段，同时保留原始 take 和撤销历史。未录完的片段只保存成新 take，不覆盖原轨。</p><h3>03 / 曲线与精修</h3><p>Shift 单击波形添加增益自动化。“音准”先分析音高，再上下拖动音符设置目标。请确认歌曲调性，非调内音与转调需要人工处理。效果和调音为离线渲染，输出新音轨以便对比。</p><h3>04 / 输出</h3><p>静音 / 独奏、片段起点、声像与自动化都会进入离线混音。导出采用双遍 LUFS / True-Peak 处理。WAV 为 48 kHz / 24-bit，MP3 为 320 kbps。浏览器“试听总音量”不影响导出。</p><div class="keyboard-table"><kbd>Space</kbd><span>播放 / 暂停</span><kbd>R</kbd><span>开始 / 停止录音</span><kbd>Ctrl/⌘ + Z</kbd><span>撤销</span><kbd>Ctrl/⌘ + Shift + Z</kbd><span>重做</span><kbd>Shift + 单击波形</kbd><span>添加音量曲线节点</span><kbd>Escape</kbd><span>关闭弹窗 / 舞台</span></div><p>软件监听存在设备延迟，推荐声卡直通监听。模型首轮下载需要联网；音频处理不调用云端服务。不要把当前本地单用户服务直接暴露到公网。</p>`);}
function exportDialog(){writable();const p=current();const solos=p.tracks.some(t=>t.solo);const audible=p.tracks.filter(t=>!t.muted&&(!solos||t.solo));if(!audible.length)throw new Error('没有可听轨道。请先取消静音。');modal('让作品，离开工作台。','EXPORT MASTER',`<div class="export-summary">${esc(p.name)}<br/>${clock(p.duration)} / 48,000 Hz<br/>${audible.map(t=>esc(t.name)).join(' + ')}</div><div class="export-options"><label for="export-format">输出格式</label><select id="export-format"><option value="wav">WAV · 24-bit 无损母带</option><option value="flac">FLAC · 24-bit 无损压缩</option><option value="mp3">MP3 · 320 kbps</option></select>${range('export-lufs','目标综合响度',-14,-24,-9,1,' LUFS')}</div><p>双遍标准化，目标真峰值 −1 dBTP。MP3 编码可能引入额外的重建峰值；正式母带建议保留 WAV / FLAC。</p><button class="primary full" data-action="render-export">渲染与导出 ↗</button><p class="small">原唱参考是否进入成品，由当前静音 / 独奏状态决定。</p>`);}
function showExportResult(j:Job){modal('你的版本，已就绪。','MASTER COMPLETE',`<p>混音与母带处理已完成。音轨的原始文件与编辑历史仍保留在本地工程中。</p><a class="primary export-link" href="/api/jobs/${j.id}/download">下载成品 ↗</a><details class="advanced"><summary>查看本次母带测量报告</summary><pre class="detail-json">${esc(JSON.stringify(j.result?.metadata.mastering||{},null,2))}</pre></details>`);}
async function importFile(file:File){writable();engine.stop();closeModal();state.busy=true;state.uploadProgress=0;state.uploadLabel='上传原版歌曲…';renderJob();renderButtons();
  try{const p=await upload<Project>('/projects/import',file,file.name,r=>{state.uploadProgress=r;state.uploadLabel=r===1?'上传完成 · 正在解码与生成波形':'上传原版歌曲…';renderJob();});state.jobs=[];latestJob=null;setProject(p);toast('歌曲已导入。下一步：分离原唱与伴奏。');}
  finally{state.busy=false;state.uploadProgress=null;renderJob();renderButtons();}
}
async function demo(){writable();engine.stop();closeModal();state.busy=true;renderButtons();toast('正在生成不含商业歌曲的合成演示。');try{const p=await api<Project>('/projects/demo','POST');state.jobs=[];latestJob=null;setProject(p);toast('合成演示已载入。示例伴奏和旋律不是 AI 分离结果。');}finally{state.busy=false;renderButtons();}}
async function play(){if(state.capturing){engine.stopRecording();return;}if(state.busy||state.uploadProgress!==null)return;const p=current();if(engine.playing){engine.stop();return;}let start=engine.parked>=p.duration-.02?0:engine.parked;if(state.loop&&state.selection)start=state.selection[0];state.busy=true;renderButtons();try{await engine.play(p,start,state.loop&&state.selection?state.selection[1]:p.duration);}finally{state.busy=false;renderButtons();}}
async function record(punch=false){
  if(state.capturing){engine.stopRecording();return;}writable();const p=current();
  if(p.duration<.1)throw new Error('工程没有可录制的时长');
  const range_=punch?state.selection:null;
  if(punch&&!range_)throw new Error('拖动波形选择需要补录的片段。');
  const target=punch?selected():null;
  if(target&&['original','reference','accompaniment'].includes(target.role))throw new Error('请选择你录制的人声轨，不要覆盖原曲或伴奏。');
  const start=range_?range_[0]:(engine.parked>=p.duration-.1?0:engine.parked),end=range_?range_[1]:p.duration;
  if(end-start<.08)throw new Error('录制片段太短。');
  if(target&&(start<target.offset||end>target.offset+target.duration+.01))throw new Error('补录选区必须位于所选人声片段内部。');
  state.busy=true;renderButtons();
  try{if(!engine.micReady)await engine.connectMic(state.deviceIn);}finally{state.busy=false;renderButtons();}
  state.capturing=true;state.loop=false;renderButtons();
  let capture:Capture;
  try{capture=await engine.record(target?{...p,tracks:p.tracks.map(t=>t.id===target.id?{...t,muted:true,solo:false}:t)}:p,start,end,state.latency,state.countIn);}
  finally{engine.stop();state.capturing=false;renderButtons();}
  if(capture.duration<.03){toast('录音已取消，没有保存空片段。');return;}
  state.unsaved={capture,projectId:p.id,offset:start};
  const complete=capture.duration>=end-start-.03;
  const saved=await saveCapture(capture,p.id,p.revision,start,target&&complete?target.id:null,target&&complete?end:null);
  if(saved&&target&&!complete)toast('补录提前停止：已保存为独立 take，没有覆盖原轨。');
}
async function saveCapture(capture:Capture,projectId:string,revision:number,offset:number,target:string|null,end:number|null){
  state.uploadProgress=0;state.uploadLabel='保存原始 PCM take…';renderJob();renderButtons();
  try{
    const query=new URLSearchParams({revision:String(revision),offset:String(offset)});if(target&&end!==null){query.set('target',target);query.set('end',String(end));}
    const result=await upload<{project:Project;job:Job|null;track_id:string}>(`/projects/${projectId}/recordings?${query}`,capture.blob,'take.wav',r=>{state.uploadProgress=r;state.uploadLabel=r===1?'已上传 · 保存波形与工程':'保存原始 PCM take…';renderJob();});
    state.unsaved=null;if(pendingCaptureURL){URL.revokeObjectURL(pendingCaptureURL);pendingCaptureURL='';}state.selected=result.track_id;setProject(result.project);
    if(result.job){state.jobs.unshift(result.job);latestJob=result.job;}else toast('原始 take 已保存。');
    return true;
  }catch(error){
    if(pendingCaptureURL)URL.revokeObjectURL(pendingCaptureURL);pendingCaptureURL=URL.createObjectURL(capture.blob);
    modal('录音仍在，请先保存。','UNSAVED TAKE',`<p>${esc(error instanceof Error?error.message:String(error))}</p><p>服务器未确认保存。你的原始录音仍在本页面内存中；关闭页面前请下载 WAV，或重试保存为独立 take。</p><a class="primary export-link" href="${pendingCaptureURL}" download="simple-ktv-unsaved-take.wav">下载原始录音 ↗</a><button class="outline full" data-action="retry-capture">重试保存为新 take</button><button class="quiet full" data-action="discard-capture">已另存 WAV，放弃页面副本</button>`);
    return false;
  }finally{state.uploadProgress=null;renderJob();renderButtons();}
}
async function populateDevices(){
  if(!navigator.mediaDevices?.enumerateDevices)return;const devices=await navigator.mediaDevices.enumerateDevices();
  const input=document.getElementById('input-device') as HTMLSelectElement|null,output=document.getElementById('output-device') as HTMLSelectElement|null;
  if(input){input.innerHTML='<option value="">系统默认麦克风</option>'+devices.filter(d=>d.kind==='audioinput'&&d.deviceId&&d.deviceId!=='default').map((d,i)=>`<option value="${esc(d.deviceId)}">${esc(d.label||`输入设备 ${i+1}`)}</option>`).join('');input.value=state.deviceIn;}
  if(output){output.innerHTML='<option value="">系统默认输出</option>'+devices.filter(d=>d.kind==='audiooutput'&&d.deviceId&&d.deviceId!=='default').map((d,i)=>`<option value="${esc(d.deviceId)}">${esc(d.label||`输出设备 ${i+1}`)}</option>`).join('');output.value=state.deviceOut;output.disabled=!engine.hasSinkSelection;}
}
async function openStage(){state.stage=true;$('stage').classList.remove('hidden');try{await $('stage').requestFullscreen();}catch{/* Overlay still works when fullscreen is unsupported. */}}
async function closeStage(){state.stage=false;$('stage').classList.add('hidden');if(document.fullscreenElement)await document.exitFullscreen();}
function pitchOptions(){return {track_id:selected().id,pitch_engine:state.pitchEngine,tonic:state.tonic,scale:state.scale,strength:state.strength,retune_ms:state.retune};}

async function action(name:string,element:HTMLElement){
  switch(name){
    case 'import':writable();closeModal();$<HTMLInputElement>('audio-file').click();break;
    case 'demo':await demo();break;
    case 'library':await library();break;
    case 'models':await modelLibrary();break;
    case 'history':history();break;
    case 'help':help();break;
    case 'studio':window.scrollTo({top:0,behavior:'smooth'});break;
    case 'close-modal':closeModal();break;
    case 'open-project':{writable();engine.stop();const id=element.dataset.id!;const [p,jobs]=await Promise.all([api<Project>(`/projects/${id}`),api<Job[]>(`/projects/${id}/jobs`)]);state.jobs=jobs;latestJob=null;setProject(p);renderJob();closeModal();break;}
    case 'play':await play();break;
    case 'stop':if(state.capturing)engine.stopRecording();else{engine.stop();engine.parked=state.selection?.[0]||0;drawTimeline();}break;
    case 'record':await record();break;
    case 'punch':await record(true);break;
    case 'loop':state.loop=!state.loop;renderButtons();if(state.loop&&!state.selection)toast('拖动波形选择 AB 练习片段。');break;
    case 'key-down':writable();state.key=Math.max(-12,state.key-1);$('key-value').textContent=state.key>0?`+${state.key}`:String(state.key);break;
    case 'key-up':writable();state.key=Math.min(12,state.key+1);$('key-value').textContent=state.key>0?`+${state.key}`:String(state.key);break;
    case 'transpose':await runJob('transpose',{semitones:state.key});break;
    case 'undo':case 'redo':{writable();engine.stop();const p=current();setProject(await api<Project>(`/projects/${p.id}/${name}`,'POST',{revision:p.revision}));break;}
    case 'select-track':state.selected=element.dataset.id!;highlightTrack();renderInspector();drawTimeline();break;
    case 'mute':case 'solo':{const t=current().tracks.find(t=>t.id===element.dataset.id)!;await patchTrack(t.id,{[name==='mute'?'muted':'solo']:!t[name==='mute'?'muted':'solo']});break;}
    case 'clear-selection':state.selection=null;selectionText();drawTimeline();break;
    case 'erase':if(!state.selection)throw new Error('先拖动波形选择抹除片段');await runJob('erase',{track_id:selected().id,start:state.selection[0],end:state.selection[1]});break;
    case 'separate':await runJob('separate',{model:state.model,quality:state.quality});break;
    case 'effects':await runJob('effects',{track_id:selected().id,effects:state.fx});break;
    case 'ai-denoise':case 'ai-dereverb':await runJob('restore',{track_id:selected().id,model:name==='ai-denoise'?'denoise':'dereverb',quality:state.quality});break;
    case 'analyze':await runJob('pitch',pitchOptions());break;
    case 'tune':await runJob('tune',{...pitchOptions(),notes:[...state.edited].map(i=>state.notes[i]).filter(Boolean)});break;
    case 'harmony':if(!state.voices.length||state.voices.length>3)throw new Error('请选择 1–3 条和声');if(state.scale==='chromatic')throw new Error('音阶和声需要选择大调或小调。');await runJob('harmony',{...pitchOptions(),voices:state.voices});break;
    case 'note-up':case 'note-down':{writable();const n=state.notes[state.noteSelected];if(!n)throw new Error('先在音高图上选择一个音符');n.midi=Math.max(24,Math.min(108,n.midi+(name==='note-up'?1:-1)));state.edited.add(state.noteSelected);drawPitch();break;}
    case 'clear-automation':await patchTrack(selected().id,{automation:[]});break;
    case 'delete-point':await patchTrack(selected().id,{automation:selected().automation.filter((_,i)=>i!==Number(element.dataset.index))});break;
    case 'automix':await runJob('automix');break;
    case 'offset':engine.stop();await patchTrack(selected().id,{offset:Number($<HTMLInputElement>('mix-offset').value)});break;
    case 'rename':{const t=selected(),name=window.prompt('音轨名称',t.name);if(name?.trim())await patchTrack(t.id,{name:name.trim()});break;}
    case 'delete-track':{writable();const p=current(),t=selected();if(!window.confirm(`从工程移除“${t.name}”？原始资产仍保留，可撤销。`))break;engine.stop();setProject(await api<Project>(`/projects/${p.id}/tracks/${t.id}?revision=${p.revision}`,'DELETE'));break;}
    case 'connect-mic':if(state.capturing)throw new Error('请先停止录音');await engine.connectMic(state.deviceIn);state.tab='devices';renderInspector();toast('麦克风已连接，监听默认关闭。');break;
    case 'export':exportDialog();break;
    case 'render-export':{const format=$<HTMLSelectElement>('export-format').value,target_lufs=Number($<HTMLInputElement>('export-lufs').value);closeModal();await runJob('export',{format,target_lufs});break;}
    case 'lyrics':writable();current();$<HTMLInputElement>('lyric-file').click();break;
    case 'stage':await openStage();break;
    case 'close-stage':await closeStage();break;
    case 'cancel-job':await api(`/jobs/${element.dataset.id!}/cancel`,'POST');await poll();break;
    case 'dismiss-job':latestJob=null;renderJob();break;
    case 'discard-capture':if(window.confirm('确认已另存 WAV？此操作会清除页面中的未保存录音，不能撤销。')){state.unsaved=null;if(pendingCaptureURL)URL.revokeObjectURL(pendingCaptureURL);pendingCaptureURL='';closeModal();renderButtons();}break;
    case 'retry-capture':{const u=state.unsaved;if(!u)break;const p=await api<Project>(`/projects/${u.projectId}`);closeModal();await saveCapture(u.capture,p.id,p.revision,u.offset,null,null);break;}
  }
}

document.addEventListener('click',event=>{
  const target=event.target as HTMLElement;
  const tab=target.closest<HTMLElement>('[data-tab]');if(tab){state.tab=tab.dataset.tab as Tab;renderInspector();return;}
  const element=target.closest<HTMLElement>('[data-action]');if(!element||(element instanceof HTMLButtonElement&&element.disabled))return;
  event.preventDefault();void action(element.dataset.action!,element).catch(fail);
});
document.addEventListener('input',event=>{
  const element=event.target as HTMLInputElement;const id=element.id,value=Number(element.value);
  const output=document.getElementById(id+'-value');if(output)output.textContent=element.value+(element.dataset.unit||'');
  if(id.startsWith('fx-')&&element.type==='range'){state.fx[id.slice(3)]=value;drawEq();}
  if(id==='pitch-strength')state.strength=value;if(id==='pitch-retune')state.retune=value;
  if(id==='latency')state.latency=value;
  if(id==='monitor-volume')engine.setVolume(value);
  if(id==='zoom'){state.zoom=value;$('timeline-inner').style.width=`${value*100}%`;drawTimeline();}
});
document.addEventListener('change',event=>{
  const element=event.target as HTMLInputElement|HTMLSelectElement;const id=element.id;
  const run=async()=>{
    if(id==='fx-preset'){state.preset=element.value;state.fx={...presets[state.preset]!};renderInspector();}
    if(id==='separation-model')state.model=element.value;if(id==='separation-quality')state.quality=element.value;
    if(id==='pitch-engine')state.pitchEngine=element.value;
    if(id==='tonic'){state.tonic=Number(element.value);if(state.project)await patchProject({tonic:state.tonic});}
    if(id==='scale'){state.scale=element.value;if(state.project)await patchProject({scale:state.scale});}
    if(id==='mix-gain')await patchTrack(selected().id,{gain_db:Number(element.value)});
    if(id==='mix-pan')await patchTrack(selected().id,{pan:Number(element.value)});
    if(id==='count-in')state.countIn=Number(element.value);
    if(id==='bpm'&&state.project)await patchProject({bpm:Number(element.value)});
    if(id==='input-device'){await engine.connectMic(element.value);state.deviceIn=element.value;renderInspector();}
    if(id==='output-device'){await engine.setOutput(element.value);state.deviceOut=element.value;}
    if(id==='mic-monitor'){
      const input=element as HTMLInputElement;
      if(input.checked&&!window.confirm('请先戴上耳机。软件监听会有延迟，外放可能产生啸叫。确认开启？')){input.checked=false;return;}
      if(input.checked&&!engine.micReady){await engine.connectMic(state.deviceIn);}
      engine.monitorMic(input.checked);
    }
    if(element.dataset.voice){const v=Number(element.dataset.voice);state.voices=(element as HTMLInputElement).checked?[...new Set([...state.voices,v])]:state.voices.filter(x=>x!==v);}
  };void run().catch(fail);
});
$<HTMLInputElement>('audio-file').addEventListener('change',e=>{const input=e.target as HTMLInputElement;const file=input.files?.[0];input.value='';if(file)void importFile(file).catch(fail);});
$<HTMLInputElement>('lyric-file').addEventListener('change',e=>{const input=e.target as HTMLInputElement;const file=input.files?.[0];input.value='';if(file)void file.text().then(text=>patchProject({lyrics:text})).then(()=>toast('LRC 歌词已导入。')).catch(fail);});
for(const event of ['dragenter','dragover'])document.addEventListener(event,e=>{const drag=e as DragEvent;if(drag.dataTransfer?.types.includes('Files')){e.preventDefault();$('empty-state').classList.add('drag-over');}});
document.addEventListener('dragleave',()=> $('empty-state').classList.remove('drag-over'));
document.addEventListener('drop',event=>{event.preventDefault();$('empty-state').classList.remove('drag-over');const file=event.dataTransfer?.files[0];if(file)void importFile(file).catch(fail);});
document.addEventListener('keydown',event=>{
  const target=event.target as HTMLElement;
  if(['INPUT','SELECT','TEXTAREA'].includes(target.tagName)||target.isContentEditable)return;
  if(event.key==='Escape'&&state.stage){void closeStage();return;}
  if($<HTMLDialogElement>('modal').open)return;
  let name='';if(event.code==='Space')name='play';else if(event.code==='KeyR'&&!event.ctrlKey&&!event.metaKey)name='record';else if((event.ctrlKey||event.metaKey)&&event.code==='KeyZ')name=event.shiftKey?'redo':'undo';
  if(name){event.preventDefault();void action(name,$('play-button')).catch(fail);}
});
$<HTMLDialogElement>('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal();});
window.addEventListener('beforeunload',e=>{if(state.capturing||state.unsaved){e.preventDefault();e.returnValue='';}});
window.addEventListener('resize',()=>{drawTimeline();drawEq();drawPitch();});
navigator.mediaDevices?.addEventListener('devicechange',()=>void populateDevices());
engine.onDeviceLost=()=>toast('麦克风已断开。当前已录制的片段会尝试保存。',true);

const samples=new Float32Array(2048);
function level(analyser:AnalyserNode|null):number{if(!analyser)return -100;analyser.getFloatTimeDomainData(samples);let peak=0;for(const sample of samples)peak=Math.max(peak,Math.abs(sample));return 20*Math.log10(Math.max(peak,1e-5));}
function animate(time:number){
  if(!document.hidden){
    const playing=engine.playing,position=engine.position();
    if(time-lastCanvasRender>34){tide.draw(engine.analyser,playing);if(state.stage)stageTide.draw(engine.analyser,playing);if(playing||state.capturing)drawTimeline();lastCanvasRender=time;}
    if(time-lastMeterRender>60){
      $('play-time').textContent=clock(position,true);$('play-button').textContent=playing?'Ⅱ':'▶';
      livePeak=level(engine.analyser);inputLevel=level(engine.micAnalyser);
      $('output-level').textContent=livePeak<=-90?'−∞ dB':`${livePeak.toFixed(1)} dB`;
      $('output-meter').style.width=`${Math.max(0,Math.min(100,(livePeak+60)/60*100))}%`;
      $('clip-state').textContent=livePeak>=-.2?'LIMITER / 接近削波':'PEAK METER';
      $('input-state').textContent=engine.micReady?`INPUT ${inputLevel<=-90?'−∞':inputLevel.toFixed(1)} dB`:'INPUT / 未连接';
      const meter=document.getElementById('input-meter');if(meter)meter.style.width=`${Math.max(0,Math.min(100,(inputLevel+60)/60*100))}%`;
      if(state.capturing){const count=Math.max(0,engine.startAt-(engine.context?.currentTime||0));$('save-status').textContent=count>.05?`预备 / ${Math.ceil(count*current().bpm/60)}`:`正在录音 / ${clock(engine.capturedSeconds,true)}`;}
      const lyrics=state.project?.lyrics||[];let index=-1;for(let i=0;i<lyrics.length;i++)if(lyrics[i]!.time<=position)index=i;else break;
      const lyric=lyrics[index]?.text||'把这一刻，唱成你的版本。';$('current-lyric').textContent=lyric;
      if(state.stage){$('stage-current').textContent=lyric;$('stage-next').textContent=lyrics[index+1]?.text||'';$('stage-time').textContent=clock(position,true);}
      lastMeterRender=time;
    }
    if(playing&&!state.capturing&&position>=engine.endPosition-.003&&!loopPending){
      engine.stop();if(state.loop&&state.selection&&state.project){loopPending=true;void engine.play(state.project,state.selection[0],state.selection[1]).catch(fail).finally(()=>loopPending=false);}
    }
  }
  requestAnimationFrame(animate);
}
async function init(){
  renderInspector();renderButtons();requestAnimationFrame(animate);
  try{const [health,projects]=await Promise.all([api<Health>('/health'),api<typeof state.projects>('/projects')]);state.health=health;state.projects=projects;state.pitchEngine=health.torchcrepe?'torchcrepe':'pyin';lastHealth=performance.now();renderHealth();
    let last:string|null=null;try{last=localStorage.getItem('simple-ktv-project');}catch{/* Optional preference. */}
    if(last&&projects.some(p=>p.id===last)){state.jobs=await api<Job[]>(`/projects/${last}/jobs`);setProject(await api<Project>(`/projects/${last}`));renderJob();}else renderInspector();
  }catch(error){$('runtime-badge').textContent='LOCAL ENGINE / OFFLINE';toast(`本地引擎连接失败：${error instanceof Error?error.message:String(error)}`,true);}
  window.setInterval(()=>void poll(),900);
}
void init();
