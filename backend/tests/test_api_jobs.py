import time
import sys
import subprocess
from app.config import Settings
from app.store import Store, uid
from app.jobs import Jobs
from app import audio
from conftest import import_project, wav_bytes


def wait_job(client,id,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=client.get(f"/api/jobs/{id}").json()
        if job["state"] not in {"queued","running","cancelling"}:return job
        time.sleep(.1)
    raise AssertionError(f"Job {id} did not finish")


def submit(client,p,task,**options):
    r=client.post(f'/api/projects/{p["id"]}/jobs',json={"revision":p["revision"],"task":task,**options})
    assert r.status_code==202,r.text
    job=wait_job(client,r.json()["id"])
    assert job["state"]=="completed",job
    return job,client.get(f'/api/projects/{p["id"]}').json()


def test_local_security_headers(client):
    r=client.get('/api/health');assert r.status_code==200
    assert 'gpu' in r.json() and 'device' in r.json()
    assert 'frame-ancestors' in r.headers['content-security-policy']
    assert client.post('/api/projects/demo',headers={'X-KTV-Client':''}).status_code==403
    assert client.post('/api/projects/demo',headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.get('/api/health',headers={'Host':'attacker.invalid'}).status_code==400


def test_import_range_assets_and_validation(client):
    p=import_project(client)
    assert p['duration']==2 and len(p['tracks'])==1
    asset=p['tracks'][0]['asset_id']
    r=client.get(f'/api/projects/{p["id"]}/assets/{asset}',headers={'Range':'bytes=0-43'})
    assert r.status_code==206 and len(r.content)==44 and r.content[:4]==b'RIFF'
    q=import_project(client)
    assert client.get(f'/api/projects/{q["id"]}/assets/{asset}').status_code==404
    assert client.post('/api/projects/import',files={'file':('x.txt',b'x')}).status_code==422
    assert client.post('/api/projects/import',files={'file':('x.mp3',b'')}).status_code==422
    assert client.post('/api/projects/import',files={'file':('x.mp3',b'bad mp3')}).status_code==422
    assert client.post('/api/projects/import',files={'file':('large.wav',b'x'*(2*1024*1024+1))}).status_code==422


def test_mp3_import_is_real_decode(client,tmp_path):
    wav=tmp_path/'source.wav';wav.write_bytes(wav_bytes())
    mp3=tmp_path/'source.mp3';audio.command(['ffmpeg','-v','error','-y','-i',str(wav),str(mp3)])
    r=client.post('/api/projects/import',files={'file':('song.mp3',mp3.read_bytes(),'audio/mpeg')})
    assert r.status_code==201 and abs(r.json()['duration']-2)<.03


def test_revision_undo_and_metadata_round_trip(client):
    p=import_project(client);id=p['id'];t=p['tracks'][0]
    patch={'revision':p['revision'],'gain_db':-5,'pan':.2,'automation':[{'time':0,'db':-3},{'time':1,'db':0}]}
    r=client.patch(f'/api/projects/{id}/tracks/{t["id"]}',json=patch);assert r.status_code==200
    p=r.json();assert p['tracks'][0]['gain_db']==-5
    assert client.patch(f'/api/projects/{id}/tracks/{t["id"]}',json=patch).status_code==409
    u=client.post(f'/api/projects/{id}/undo',json={'revision':p['revision']}).json()
    assert u['tracks'][0]['gain_db']==0 and u['can_redo']
    r=client.post(f'/api/projects/{id}/redo',json={'revision':u['revision']}).json()
    assert r['tracks'][0]['gain_db']==-5
    manifest=client.get(f'/api/projects/{id}/manifest')
    assert manifest.status_code==200 and 'attachment' in manifest.headers['content-disposition']


def test_erase_job_and_undo_keep_original_asset(client):
    p=import_project(client);old=p['tracks'][0]['asset_id'];t=p['tracks'][0]
    old_bytes=client.get(f'/api/projects/{p["id"]}/assets/{old}').content
    job,p=submit(client,p,'erase',track_id=t['id'],start=.5,end=1)
    assert p['tracks'][0]['asset_id']!=old
    assert client.get(f'/api/projects/{p["id"]}/assets/{old}').content==old_bytes
    p=client.post(f'/api/projects/{p["id"]}/undo',json={'revision':p['revision']}).json()
    assert p['tracks'][0]['asset_id']==old
    assert client.get(f'/api/jobs/{job["id"]}/log').status_code==200


def test_transpose_uses_root_and_moves_declared_tonic(client):
    p=import_project(client);root=p['tracks'][0]['asset_id']
    _,p=submit(client,p,'transpose',semitones=3)
    assert p['key_shift']==3 and p['tonic']==3 and p['tracks'][0]['source_asset_id']==root
    _,p=submit(client,p,'transpose',semitones=0)
    assert p['key_shift']==0 and p['tonic']==0 and p['tracks'][0]['source_asset_id']==root


def test_record_and_punch_preserve_raw_takes(client):
    p=import_project(client)
    r=client.post(f'/api/projects/{p["id"]}/recordings?revision={p["revision"]}',files={'file':('take.wav',wav_bytes())})
    assert r.status_code==201;r=r.json();p=r['project'];t=p['tracks'][-1];root=t['asset_id']
    r=client.post(f'/api/projects/{p["id"]}/recordings?revision={p["revision"]}&offset=.5&end=1.0&target={t["id"]}',files={'file':('take.wav',wav_bytes(.5,330))})
    assert r.status_code==201,r.text
    job=wait_job(client,r.json()['job']['id']);assert job['state']=='completed',job
    p=client.get(f'/api/projects/{p["id"]}').json()
    assert len(p['tracks'])==3 and p['tracks'][-1]['muted']
    assert p['tracks'][1]['asset_id']!=root and p['tracks'][1]['source_asset_id']==root


def test_short_punch_fails_without_losing_raw_or_original(client):
    p=import_project(client)
    r=client.post(f'/api/projects/{p["id"]}/recordings?revision={p["revision"]}',files={'file':('take.wav',wav_bytes())}).json()
    p=r['project'];t=p['tracks'][-1];old=t['asset_id']
    r=client.post(f'/api/projects/{p["id"]}/recordings?revision={p["revision"]}&offset=.5&end=1.0&target={t["id"]}',files={'file':('take.wav',wav_bytes(.1))})
    job=wait_job(client,r.json()['job']['id']);assert job['state']=='failed'
    p=client.get(f'/api/projects/{p["id"]}').json()
    assert p['tracks'][1]['asset_id']==old and len(p['tracks'])==3


def test_effects_automix_export_actual_worker(client):
    p=import_project(client,4)
    _,p=submit(client,p,'effects',track_id=p['tracks'][0]['id'])
    assert len(p['tracks'])==2 and p['tracks'][0]['muted']
    _,p=submit(client,p,'automix')
    job,p=submit(client,p,'export',format='wav',target_lufs=-14)
    r=client.get(f'/api/jobs/{job["id"]}/download')
    assert r.status_code==200 and r.content[:4]==b'RIFF' and len(r.content)>1000
    assert 'output' not in client.get(f'/api/jobs/{job["id"]}').json()


def test_neural_feature_does_not_fake_success_when_absent(client):
    p=import_project(client);caps=client.get('/api/health').json()
    if not caps['separation']:
        r=client.post(f'/api/projects/{p["id"]}/jobs',json={'revision':p['revision'],'task':'separate'})
        assert r.status_code==503
    if not caps['torchcrepe']:
        r=client.post(f'/api/projects/{p["id"]}/jobs',json={'revision':p['revision'],'task':'pitch','track_id':p['tracks'][0]['id'],'pitch_engine':'torchcrepe'})
        assert r.status_code==503


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    s=Store(tmp_path/'data');p=s.create('restart');id=uid()
    s.save_job({'id':id,'project':p['id'],'task':'pitch','state':'running'})
    manager=Jobs(s,Settings(data=tmp_path/'data',models=tmp_path/'models'))
    try:assert s.job(id)['state']=='failed' and 'restarted' in s.job(id)['error']
    finally:manager.close()


def test_cancellation_terminates_real_isolated_process(client,monkeypatch):
    p=import_project(client)
    original=subprocess.Popen
    def sleeping_worker(args,**kwargs):
        return original([sys.executable,'-c','import time; time.sleep(60)'],**kwargs)
    monkeypatch.setattr('app.jobs.subprocess.Popen',sleeping_worker)
    r=client.post(f'/api/projects/{p["id"]}/jobs',json={'revision':p['revision'],'task':'erase','track_id':p['tracks'][0]['id'],'start':0,'end':1})
    assert r.status_code==202;id=r.json()['id']
    time.sleep(.15)
    assert client.patch(f'/api/projects/{p["id"]}',json={'revision':p['revision'],'name':'locked'}).status_code==409
    assert client.post(f'/api/jobs/{id}/cancel').status_code==200
    assert wait_job(client,id)['state']=='cancelled'
    assert client.get(f'/api/projects/{p["id"]}').json()['revision']==p['revision']


def test_pitch_and_harmony_use_actual_dsp_worker(client):
    p=import_project(client,2)
    t=p['tracks'][0]
    _,p=submit(client,p,'pitch',track_id=t['id'],pitch_engine='pyin')
    assert p['tracks'][0]['pitch']['engine']=='pyin'
    assert p['tracks'][0]['pitch']['notes']
    _,p=submit(client,p,'harmony',track_id=t['id'],pitch_engine='pyin',voices=[2,4])
    harmonies=[track for track in p['tracks'] if track['role']=='harmony']
    assert len(harmonies)==2 and harmonies[0]['pan']<0<harmonies[1]['pan']
    for h in harmonies:
        assert len(client.get(f'/api/projects/{p["id"]}/assets/{h["asset_id"]}').content)>1000


def test_zero_strength_tuning_preserves_exact_audio_file(client):
    p=import_project(client,2)
    t=p['tracks'][0]
    original=client.get(f'/api/projects/{p["id"]}/assets/{t["asset_id"]}').content
    _,p=submit(client,p,'tune',track_id=t['id'],pitch_engine='pyin',strength=0)
    assert p['tracks'][0]['muted']
    tuned=client.get(f'/api/projects/{p["id"]}/assets/{p["tracks"][-1]["asset_id"]}').content
    assert tuned==original
