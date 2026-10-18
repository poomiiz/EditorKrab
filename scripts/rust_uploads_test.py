"""Chunk upload/restart acceptance on disposable state. Python is not a runtime dependency."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from rust_jobs_test import ROOT, launch, port, ready, request, stop


def chunk(base, identifier, offset, body):
    req = urllib.request.Request(base+f"/api/v1/sources/uploads/{identifier}?offset={offset}",
                                 data=body, method="POST", headers={"Content-Type":"application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main():
    chunk_size=4*1024*1024
    buffer=io.BytesIO()
    with wave.open(buffer,"wb") as audio:
        audio.setparams((1,2,16000,0,"NONE","not compressed"))
        audio.writeframes(b"\0"*(chunk_size+512))
    payload=buffer.getvalue()
    identifier=str(uuid.uuid4())
    body={"id":identifier,"project":"upload-test","name":"ชุดฟุต/เสียง ทดสอบ.wav",
          "size":len(payload),"last_modified":123}
    checks=[]
    with tempfile.TemporaryDirectory(prefix="editorkrab-upload-http-") as temporary:
        state=Path(temporary)/"state.sqlite"
        number=port();base=f"http://127.0.0.1:{number}"
        process=launch(state,number)
        try:
            ready(process,base)
            assert request(base,"POST","/api/v1/sources/uploads",body)==(200,{"offset":0,"size":len(payload)})
            assert chunk(base,identifier,0,payload[:chunk_size])[0]==200
            assert request(base,"POST","/api/v1/sources/uploads",{**body,"project":"forked-draft"})[0]==200
            assert request(base,"POST",f"/api/v1/sources/uploads/{identifier}/finish")[0]==409
            assert chunk(base,identifier,0,b"x"*chunk_size)[0]==409
            assert chunk(base,identifier,chunk_size+1,b"x")[0]==409
            checks += ["Thai relative display name", "incomplete finish blocked", "changed replay rejected", "gap blocked"]
            stop(process)
            process=launch(state,number);ready(process,base)
            status,resumed=request(base,"POST","/api/v1/sources/uploads",body)
            assert status==200 and resumed["offset"]==chunk_size
            assert chunk(base,identifier,0,payload[:chunk_size])[0]==200
            assert chunk(base,identifier,chunk_size,payload[chunk_size:])[0]==200
            status,source=request(base,"POST",f"/api/v1/sources/uploads/{identifier}/finish")
            assert status==200 and Path(source["path"]).read_bytes()==payload
            assert source["source_id"]==identifier and source["name"]==body["name"]
            checks += ["backend restart resumes durable offset", "byte-identical replay accepted", "exact completed bytes"]
            stop(process)
            process=launch(state,number);ready(process,base)
            assert request(base,"POST",f"/api/v1/sources/uploads/{identifier}/finish")== (200,source)
            assert chunk(base,identifier,0,payload[:chunk_size])[0]==200
            assert request(base,"POST","/api/v1/sources/uploads",{**body,"size":len(payload)+1})[0]==409
            assert request(base,"POST","/api/v1/sources/uploads",{**body,"id":"../escape"})[0]==400
            assert request(base,"POST","/api/v1/sources/uploads",body,{"Origin":"https://external.invalid"})[0]==403
            checks += ["completed source survives restart", "completed replay idempotent", "draft fork can reuse source", "changed request blocked", "path traversal blocked", "foreign origin blocked"]
            draft={"step":1,"title":"ทดสอบลากฟุต","language":"auto","files":[source],"plan":"","style":"bold16"}
            saved={"operation_id":"registered","expected_revision":0,"document":draft}
            assert request(base,"PUT","/api/v1/drafts/upload-test",saved)[0]==200
            assert request(base,"PUT","/api/v1/drafts/upload-test",saved)[0]==200
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                _,jobs=request(base,"GET","/api/v1/projects/upload-test/jobs")
                if jobs and jobs[0]["status"] in ("succeeded","failed"): break
                time.sleep(.1)
            assert len(jobs)==1 and jobs[0]["status"]=="succeeded",jobs
            checks += ["completed upload automatically probes once"]
            evidence={"checks":checks,"source_id":identifier,"sha256":hashlib.sha256(payload).hexdigest(),
                      "bytes":len(payload),"probe":jobs[0]["result"],"platform":"Windows local test"}
        finally:
            stop(process)
    output=ROOT/"output/rust-uploads-native-readback.json"
    output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
    # Reusable generated media for browser acceptance, never a user source.
    (ROOT/"output/rust-upload-test.wav").write_bytes(payload)
    print(json.dumps({"checks":len(checks),"bytes":len(payload),"evidence":str(output)}))


if __name__=="__main__":
    main()
