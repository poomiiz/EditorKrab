"""Real queued native ASR acceptance on isolated state/media; Python is test tooling only."""
from __future__ import annotations
import copy, hashlib, json, os
from pathlib import Path
import shutil, tempfile, time
from rust_jobs_test import EXE, ROOT, port, request, launch, ready


def wait(base, project, predicate, timeout=90):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        status,jobs=request(base,'GET',f'/api/v1/projects/{project}/jobs')
        assert status==200
        if predicate(jobs): return jobs
        time.sleep(.025)
    raise AssertionError(jobs)


def main():
    # like rust/tests/native_asr.rs (#[ignore]): needs a real whisper engine, model and clip, which CI does not have
    if 'EDITORKRAB_ASR_TEST_ENGINE' not in os.environ:
        print('SKIP native ASR queue: set EDITORKRAB_ASR_TEST_ENGINE, EDITORKRAB_ASR_TEST_MODEL and EDITORKRAB_ASR_TEST_SOURCE'); return
    engine=Path(os.environ['EDITORKRAB_ASR_TEST_ENGINE']).resolve()
    original_model=Path(os.environ['EDITORKRAB_ASR_TEST_MODEL']).resolve()
    fixture=Path(os.environ['EDITORKRAB_ASR_TEST_SOURCE']).resolve()
    ffmpeg=shutil.which('ffmpeg'); assert ffmpeg
    with tempfile.TemporaryDirectory(prefix='ek-asr-queue-') as tmp:
        root=Path(tmp);model=root/'model.bin';shutil.copyfile(original_model,model)
        paths=[root/'บทพูดหนึ่ง.wav',root/'บทพูดสอง.wav']
        for path in paths:shutil.copyfile(fixture,path)
        files=[dict(source_id=f'source-{n}',name=p.name,path=str(p),size=p.stat().st_size,modified_ns=str(p.stat().st_mtime_ns)) for n,p in enumerate(paths)]
        state=root/'state.sqlite';number=port();base=f'http://127.0.0.1:{number}';project='asr-queue'
        env={'EDITORKRAB_WHISPER':str(engine),'EDITORKRAB_WHISPER_MODEL':str(model),'EDITORKRAB_FFMPEG':ffmpeg}
        old={key:os.environ.get(key) for key in env};os.environ.update(env)
        process=None
        def start():
            process=launch(state,number);ready(process,base);return process
        def stop():
            if process is not None and process.poll() is None:process.terminate();process.wait(timeout=15)
        try:
            process=start()
            status,settings=request(base,'GET','/api/v1/transcription/settings');assert status==200 and settings
            assert settings['model']['sha256']==hashlib.sha256(model.read_bytes()).hexdigest()
            status,_=request(base,'PUT',f'/api/v1/drafts/{project}',dict(operation_id='initial',expected_revision=0,document=dict(files=files,language='auto')));assert status==200
            wait(base,project,lambda jobs:len(jobs)==2 and all(j['status']=='succeeded' for j in jobs))
            def batch(run, selected=files):
                return dict(request_id=run,revision=1,items=[dict(key=f'file-{n}',task=dict(kind='transcribe',path=f['path'],context=dict(run_id=run,source=dict(id=f['source_id'],size=f['size'],modified_ns=f['modified_ns']),start_ms=0,end_ms=11000,language='auto'),settings=settings)) for n,f in enumerate(selected)])
            first=batch('first')
            bad=copy.deepcopy(first);bad['items'][0]['task']['context']['source']['id']='not-selected'
            assert request(base,'POST',f'/api/v1/projects/{project}/jobs',bad)[0]==400
            bad=copy.deepcopy(first);bad['items'][0]['task']['context']['end_ms']=20000
            assert request(base,'POST',f'/api/v1/projects/{project}/jobs',bad)[0]==400
            bad=copy.deepcopy(first);bad['items'][0]['task']['settings']['model']['sha256']='0'*64
            assert request(base,'POST',f'/api/v1/projects/{project}/jobs',bad)[0]==409
            status,queued=request(base,'POST',f'/api/v1/projects/{project}/jobs',first);assert status==200 and len(queued)==2
            assert {j['id'] for j in request(base,'POST',f'/api/v1/projects/{project}/jobs',first)[1]}=={j['id'] for j in queued}
            jobs=wait(base,project,lambda jobs:sum(j['status']=='succeeded' and j['task']['kind']=='transcribe' for j in jobs)>=1 and any(j['status'] in ['queued','running'] and j['task']['kind']=='transcribe' for j in jobs))
            succeeded=next(j for j in jobs if j['status']=='succeeded' and j['task']['kind']=='transcribe')
            saved_id=succeeded['id'];saved_result=succeeded['result'];stop();process=start()
            jobs=wait(base,project,lambda jobs:all(j['status']=='succeeded' for j in jobs))
            assert next(j for j in jobs if j['id']==saved_id)['result']==saved_result
            asr=[j for j in jobs if j['task']['kind']=='transcribe'];assert len(asr)==2
            assert all(j['result']['transcript']['detected_language']=='en' for j in asr)
            assert all(any('country' in s['text'] for s in j['result']['transcript']['segments']) for j in asr)
            assert all(j['result']['source']['sha256']==hashlib.sha256(fixture.read_bytes()).hexdigest() for j in asr)
            status,replayed=request(base,'POST',f'/api/v1/projects/{project}/jobs',first);assert status==200 and len(replayed)==2
            # Cancellation is durable and retry uses the same ID.
            status,cancelled=request(base,'POST',f'/api/v1/projects/{project}/jobs',batch('cancel',files[:1]));assert status==200
            identity=cancelled[0]['id'];assert request(base,'POST',f'/api/v1/jobs/{identity}/cancel',{})[0]==200
            wait(base,project,lambda jobs:any(j['id']==identity and j['status']=='cancelled' for j in jobs))
            assert request(base,'POST',f'/api/v1/jobs/{identity}/retry',{})[0]==200
            wait(base,project,lambda jobs:any(j['id']==identity and j['status']=='succeeded' for j in jobs))
            # Keep size and mtime unchanged: content hashes must still reject changed model bytes.
            metadata=model.stat()
            with model.open('r+b') as handle:
                handle.seek(100);byte=handle.read(1);handle.seek(100);handle.write(bytes([byte[0]^1]))
            os.utime(model,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
            assert request(base,'POST',f'/api/v1/projects/{project}/jobs',batch('changed-model',files[:1]))[0]==200
            jobs=wait(base,project,lambda jobs:any(j['task']['kind']=='transcribe' and j['status']=='failed' for j in jobs))
            failed=next(j for j in jobs if j['status']=='failed');assert failed['error']=='transcription_settings_changed',failed
            evidence=dict(project=project,retained_job=saved_id,jobs=jobs,checks=['source/range/settings validation','exact duplicate request','real native Thai paths','actual restart retains success','source/model SHA256','cancel and same-ID retry','same-size/mtime model change rejected'])
            (ROOT/'output'/'rust-asr-queue-readback.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'checks':evidence['checks'],'jobs':len(jobs)},ensure_ascii=False))
        finally:
            stop()
            for key,value in old.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value
            # A hard-killed parent can leave a short native child finishing its own isolated scratch.
            time.sleep(2)

if __name__=='__main__':main()
