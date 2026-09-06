export interface Point { time: number; db: number }
export interface Note { start: number; end: number; midi: number }
export interface Pitch {
  engine: string; times: number[]; midi: (number|null)[]; target: (number|null)[]|null;
  notes: Note[]; source_asset_id: string; monophonic_only: boolean;
}
export interface Asset {
  id: string; duration: number; frames: number; sample_rate: number; channels: number;
  peaks: [number, number][]; peak_db: number; rms_db: number; sha256: string;
}
export interface Track {
  id: string; asset_id: string; source_asset_id: string; name: string; role: string;
  offset: number; duration: number; gain_db: number; pan: number; muted: boolean; solo: boolean;
  automation: Point[]; transpose: number; pitch: Pitch|null;
}
export interface Project {
  id: string; name: string; revision: number; duration: number; sample_rate: number;
  bpm: number; tonic: number; scale: 'major'|'minor'|'chromatic'; key_shift: number;
  tracks: Track[]; assets: Record<string, Asset>; lyrics: { time:number; text:string }[];
  can_undo: boolean; can_redo: boolean;
}
export interface Job {
  id:string; project:string; task:string; state:'queued'|'running'|'cancelling'|'cancelled'|'completed'|'failed';
  progress:number|null; stage:string; error:string|null;
  result: { export:boolean; metadata: Record<string, unknown>; project_revision:number }|null;
}
export interface Health {
  device: string; gpu: { name:string; vram_gb:number; compute_capability:string }|null;
  separation:boolean; torchcrepe:boolean; ffmpeg:boolean; version:string; gpu_error:string|null;
  max_upload_mb:number; max_duration:number; cuda_runtime:string|null; torch:string|null;
}
export interface Model { id:string; name:string; filename:string; task:string; downloaded:boolean }
export const activeJob = (j:Job) => ['queued', 'running', 'cancelling'].includes(j.state);
