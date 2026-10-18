"""Whole-source Rust ASR planning/cache acceptance; all state/media are disposable."""
from __future__ import annotations
import copy, hashlib, json, os, shutil, sqlite3, tempfile, time
from pathlib import Path
from contextlib import closing
from rust_jobs_test import ROOT, launch, port, ready, request, stop
from rust_asr_queue_test import wait


def main():
    # like rust/tests/native_asr.rs (#[ignore]): needs a real whisper engine, model and clip, which CI does not have
    if 'EDITORKRAB_ASR_TEST_ENGINE' not in os.environ:
        print('SKIP native ASR start: set EDITORKRAB_ASR_TEST_ENGINE, EDITORKRAB_ASR_TEST_MODEL and EDITORKRAB_ASR_TEST_SOURCE'); return
    engine=Path(os.environ['EDITORKRAB_ASR_TEST_ENGINE']).resolve()
    original_model=Path(os.environ['EDITORKRAB_ASR_TEST_MODEL']).resolve()
    fixture=Path(os.environ['EDITORKRAB_ASR_TEST_SOURCE']).resolve()
    ffmpeg=shutil.which('ffmpeg'); assert ffmpeg
    with tempfile.TemporaryDirectory(prefix='ek-asr-start-') as tmp:
        root=Path(tmp); model=root/'model.bin';shutil.copyfile(original_model,model)
        source=root/'บทพูดทดสอบ.wav';shutil.copyfile(fixture,source)
        stamp=source.stat(); selected=dict(source_id='one',name=source.name,path=str(source),size=stamp.st_size,modified_ns=str(stamp.st_mtime_ns))
        state=root/'state.sqlite';number=port();base=f'http://127.0.0.1:{number}'
        env={'EDITORKRAB_WHISPER':str(engine),'EDITORKRAB_WHISPER_MODEL':str(model),'EDITORKRAB_FFMPEG':ffmpeg}
        previous={k:os.environ.get(k) for k in env};os.environ.update(env);process=None
        def start():
            process=launch(state,number);ready(process,base);return process
        def prepare(project,file=selected):
            assert request(base,'PUT',f'/api/v1/drafts/{project}',dict(operation_id='initial',expected_revision=0,document=dict(files=[file],language='auto')))[0]==200
            wait(base,project,lambda jobs:len(jobs)==1 and jobs[0]['status']=='succeeded')
        def begin(project,run,settings,**extra):
            body=dict(request_id=run,revision=1,source_id='one',settings=settings);body.update(extra)
            return request(base,'POST',f'/api/v1/projects/{project}/transcription',body)
        def complete(project):
            jobs=wait(base,project,lambda jobs:any(j['task']['kind']=='transcribe' and j['status'] in ['succeeded','failed','cancelled'] for j in jobs))
            return next(j for j in jobs if j['task']['kind']=='transcribe')
        try:
            process=start();settings=request(base,'GET','/api/v1/transcription/settings')[1];prepare('first')
            assert begin('first','bad-revision',settings,revision=2)[0]==409
            assert begin('first','bad-source',settings,source_id='missing')[0]==400
            bad=copy.deepcopy(settings);bad['model']['sha256']='0'*64
            assert begin('first','bad-settings',bad)[0]==409
            status,queued=begin('first','run-one',settings);assert status==200 and len(queued)==1,(status,queued)
            saved_id=queued[0]['id'];assert begin('first','run-one',settings)[1][0]['id']==saved_id
            first=complete('first');assert first['status']=='succeeded',first
            assert first['result']['cache_hit'] is False
            assert first['result']['transcript']['context']['end_ms']==11000
            assert first['result']['source']['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
            assert 'cache_candidate' not in first['result']
            assert begin('first','another-click',settings)[1][0]['id']==saved_id
            assert len(request(base,'GET','/api/v1/projects/first/jobs')[1])==2
            # Successful cache survives restart and rebases transcript IDs to the current request.
            stop(process);process=start();assert begin('first','run-one',settings)[1][0]['result']==first['result']
            prepare('cached');assert begin('cached','run-cache',settings)[0]==200
            cached=complete('cached');assert cached['status']=='succeeded',cached
            assert cached['result']['cache_hit'] is True
            assert cached['result']['transcript']['context']['run_id']=='run-cache'
            assert cached['result']['transcript']['segments'][0]['id']!=first['result']['transcript']['segments'][0]['id']
            # Corrections save only against exact selected source/job/segment/original text.
            draft=request(base,'GET','/api/v1/drafts/cached')[1];segment=cached['result']['transcript']['segments'][0]
            doc=copy.deepcopy(draft['document']);doc['transcript_edits']=[dict(job_id=cached['id'],segment_id=segment['id'],source_sha256=cached['result']['source']['sha256'],original_text=segment['text'],text='แก้คำทดสอบ')]
            assert request(base,'PUT','/api/v1/drafts/cached',dict(operation_id='correction',expected_revision=1,document=doc))[0]==200
            doc['transcript_edits'][0]['original_text']='not the original'
            assert request(base,'PUT','/api/v1/drafts/cached',dict(operation_id='bad-correction',expected_revision=2,document=doc))[0]==400
            # Corrupt cache is an explicit failure and never triggers fresh inference.
            with closing(sqlite3.connect(state)) as db:
                assert db.execute('select count(*) from transcription_cache').fetchone()[0]==1
                db.execute("update transcription_cache set raw=X'7b7d'");db.commit()
            prepare('corrupt');assert begin('corrupt','run-corrupt',settings)[0]==200
            corrupt=complete('corrupt');assert corrupt['status']=='failed' and corrupt['error']=='transcription_cache_corrupt',corrupt
            assert begin('corrupt','new-click',settings)[1][0]['id']==corrupt['id']
            assert begin('corrupt','run-corrupt',settings,revision=2)[0]==409
            # Even a self-consistent checksum cannot turn malformed engine JSON into a hit.
            with closing(sqlite3.connect(state)) as db:
                db.execute('update transcription_cache set sha256=?',(hashlib.sha256(b'{}').hexdigest(),));db.commit()
            prepare('malformed');assert begin('malformed','run-malformed',settings)[0]==200
            malformed=complete('malformed');assert malformed['status']=='failed' and malformed['error']=='transcription_cache_corrupt',malformed
            # Same metadata cannot conceal changed source or dependency bytes.
            with source.open('r+b') as handle:handle.seek(100);byte=handle.read(1);handle.seek(100);handle.write(bytes([byte[0]^1]))
            os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            assert begin('first','changed-source',settings)==(409,{'error':'source_changed'})
            metadata=model.stat()
            with model.open('r+b') as handle:handle.seek(100);byte=handle.read(1);handle.seek(100);handle.write(bytes([byte[0]^1]))
            os.utime(model,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
            assert begin('first','changed-model',settings)==(409,{'error':'transcription_settings_changed'})
            evidence=dict(first=first,cached=cached,corrupt=corrupt,malformed=malformed,checks=['whole-source plan','revision/source/settings rejection','same/new request dedupe','restart preserves result/cache','native cache hit rebases IDs','no raw JSON in job result','exact correction source binding','checksum and parsed-content corruption fail closed','same metadata changed bytes rejected'])
            (ROOT/'output'/'rust-asr-start-readback.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'checks':evidence['checks'],'cached_elapsed_ms':cached['result']['elapsed_ms']},ensure_ascii=False))
        finally:
            if process is not None:stop(process)
            for key,value in previous.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value
            time.sleep(1)

if __name__=='__main__':main()
