/* Frame-addressed, uncompressed microphone capture. Playback is never routed here. */
class PCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.armed=false; this.buffer=new Float32Array(4096); this.used=0; this.total=0;
    this.port.onmessage = ({data}) => {
      if (data.type==='arm') {
        this.startFrame=data.startFrame; this.endFrame=data.endFrame;
        this.used=0; this.total=0; this.armed=true;
      } else if (data.type==='stop' && this.armed) this.finish();
    };
  }
  flush() {
    if (!this.used) return;
    const samples=this.buffer.slice(0,this.used);
    this.port.postMessage({type:'data',samples,total:this.total},[samples.buffer]);
    this.used=0;
  }
  finish() {
    this.flush(); this.armed=false;
    this.port.postMessage({type:'done',total:this.total});
  }
  process(inputs, outputs) {
    // No microphone monitoring in this node, even when monitoring is enabled elsewhere.
    for (const channel of outputs[0] || []) channel.fill(0);
    if (!this.armed) return true;
    const input=inputs[0]?.[0];
    const frames=outputs[0]?.[0]?.length || 128;
    const a=Math.max(0,this.startFrame-currentFrame);
    const b=Math.min(frames,this.endFrame-currentFrame);
    for (let i=a;i<b;i++) {
      this.buffer[this.used++]=input?.[i] || 0; this.total++;
      if (this.used===this.buffer.length) this.flush();
    }
    if (currentFrame+frames>=this.endFrame) this.finish();
    return true;
  }
}
registerProcessor('pcm-recorder',PCMRecorder);
