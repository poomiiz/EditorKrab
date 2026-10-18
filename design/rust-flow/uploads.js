const CHUNK_BYTES=4*1024*1024;

async function request(url,options,signal) {
  for (let attempt=0;attempt<3;attempt++) {
    if (signal.aborted) throw signal.reason;
    const controller=new AbortController();
    const abort=()=>controller.abort();
    signal.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(abort,60000);
    try {
      const response=await fetch(url,{...options,signal:controller.signal});
      const body=await response.json();
      if (!response.ok) {
        const error=Error(body.error || 'upload_failed');error.http=true;throw error;
      }
      return body;
    } catch (error) {
      if (signal.aborted || error.http || attempt===2) throw error;
    } finally {clearTimeout(timer);signal.removeEventListener('abort',abort);}
  }
}

export async function uploadFile(project,file,pending,signal,progress) {
  const base='/api/v1/sources/uploads';
  await request(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    id:pending.upload_id,project,name:pending.name,size:pending.size,last_modified:pending.last_modified
  })},signal);
  // Re-check the committed prefix too: another file with the same name/size must never be spliced in.
  for (let offset=0;offset<file.size;offset+=CHUNK_BYTES) {
    const end=Math.min(file.size,offset+CHUNK_BYTES);
    await request(base+'/'+pending.upload_id+'?offset='+offset,{method:'POST',
      headers:{'Content-Type':'application/octet-stream'},body:file.slice(offset,end)},signal);
    progress(end,file.size);
  }
  return request(base+'/'+pending.upload_id+'/finish',{method:'POST'},signal);
}
