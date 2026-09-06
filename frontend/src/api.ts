import type { Project } from './types.js';

export async function api<T>(path:string, method='GET', data?:unknown):Promise<T> {
  const headers:Record<string,string> = { 'X-KTV-Client':'1' };
  let body:BodyInit|undefined;
  if (data instanceof FormData) body = data;
  else if (data !== undefined) { headers['Content-Type']='application/json'; body=JSON.stringify(data); }
  const response = await fetch('/api'+path, { method, headers, body });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail:response.statusText })) as { detail:unknown };
    const detail = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail);
    throw new Error(`${detail || '请求失败'} (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function upload<T>(path:string, file:File|Blob, name:string, progress:(ratio:number)=>void):Promise<T> {
  return new Promise((resolve,reject) => {
    const req = new XMLHttpRequest();
    req.open('POST', '/api'+path);
    req.setRequestHeader('X-KTV-Client', '1');
    req.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded/event.total); };
    req.onerror = () => reject(new Error('上传中断。检查本地服务是否运行。'));
    req.onload = () => {
      let result: { detail?:string };
      try { result = JSON.parse(req.responseText) as {detail?:string}; }
      catch { reject(new Error('服务器返回了无效响应')); return; }
      if(req.status>=200 && req.status<300) resolve(result as T);
      else reject(new Error(typeof result.detail==='string' ? result.detail : `上传失败 (${req.status})`));
    };
    const data = new FormData(); data.append('file',file,name); req.send(data);
  });
}

export const assetURL = (p:Project, asset:string) => `/api/projects/${p.id}/assets/${asset}`;
export const esc = (s:string) => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c] ?? c));
export function clock(s:number, decimal=false):string {
  const factor=decimal?10:1; const units=Math.round(Math.max(0,s)*factor);
  return `${String(Math.floor(units/(60*factor))).padStart(2,'0')}:${((units%(60*factor))/factor).toFixed(decimal?1:0).padStart(decimal?4:2,'0')}`;
}
export const noteName = (m:number) => `${['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][((Math.round(m)%12)+12)%12]}${Math.floor(Math.round(m)/12)-1}`;
