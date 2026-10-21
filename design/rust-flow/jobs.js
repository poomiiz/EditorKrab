// A snapshot and cursor are read together; live replay starts after that snapshot.
export function watchJobs(project, revision, container, changed=()=>{}, replaced=()=>{}, files=null) {
  let source, retryTimer, closed=false, generation=0;
  let frame=0;
  const jobs=new Map();
  const connection=document.createElement('p'); connection.setAttribute('role','status');
  const counts=document.createElement('p');
  const list=document.createElement('ul');
  container.replaceChildren(connection,counts,list);
  const labels={queued:'รอทำ',running:'กำลังทำ',succeeded:'สำเร็จ',failed:'ไม่สำเร็จ',cancelled:'ยกเลิกแล้ว'};
  const failures={source_file_missing:'ไม่พบวิดีโอต้นฉบับ',source_not_a_file:'เลือกวิดีโออีกครั้ง',
    ffprobe_start_failed:'เปิดวิดีโอไม่ได้',ffprobe_rejected_media:'อ่านวิดีโอไม่ได้',
    transcription_cache_corrupt:'ข้อความที่ถอดไว้เสีย ถอดเสียงใหม่',transcription_dependency_changed:'ไฟล์เปลี่ยนแล้ว เริ่มใหม่',transcription_settings_changed:'ตัวถอดเสียงเปลี่ยนแล้ว ถอดเสียงใหม่',transcription_not_configured:'ยังไม่ได้ติดตั้งตัวถอดเสียง',transcription_process_failed:'ถอดเสียงไม่ได้ ลองใหม่อีกครั้ง',transcription_process_timeout:'ถอดเสียงนานเกินกำหนด ลองใหม่',transcription_dependency_missing:'ยังไม่ได้ติดตั้งตัวถอดเสียง',invalid_segment_clock_or_text:'ถอดเสียงผิดพลาด ลองใหม่',ffprobe_timeout:'อ่านวิดีโอนานเกินไป ลองใหม่',media_duration_missing:'อ่านระยะเวลาไม่ได้',source_changed:'วิดีโอถูกแก้ไขแล้ว เลือกใหม่'};
  function render() {if (!frame) frame=requestAnimationFrame(()=>{frame=0;if (!closed) renderNow();});}
  function renderNow() {
    // only videos in the draft now, nothing cancelled: a removed video's jobs are history, not work
    const names=files && new Map(files().filter(f=>f?.source_id).map(f=>[f.source_id,f.name]));
    const sourceOf=job=>job.task.source?.id ?? job.task.context?.source?.id;
    const all=[...jobs.values()].filter(j=>(!names || names.has(sourceOf(j))) && j.status!=='cancelled');
    counts.textContent=all.length ? ['probe','transcribe'].map(kind=>{const group=all.filter(j=>j.task.kind===kind);return group.length ? `${kind==='probe'?'อ่านวิดีโอสำเร็จ':'ถอดเสียงสำเร็จ'} ${group.filter(j=>j.status==='succeeded').length}/${group.length}${kind==='transcribe'?' ช่วง':''}` : '';}).filter(Boolean).join(' · ') : 'ยังไม่มีงาน';
    list.replaceChildren(...all.map(job=>{
      const item=document.createElement('li');
      const name=document.createElement('span');
      name.textContent=(job.task.kind==='transcribe'?'ถอดเสียง · ':'อ่านวิดีโอ · ')+(names?.get(sourceOf(job)) || job.task.path.split(/[\\/]/).pop() || 'วิดีโอ');
      const badge=document.createElement('span');badge.className='badge';
      badge.textContent=job.cancel_requested && job.status==='running' ? 'กำลังยกเลิก' : labels[job.status] || 'ต้องตรวจสอบ';
      item.append(name,' ',badge);
      if(job.status==='running' && job.task.kind==='transcribe'){const phase=document.createElement('span');phase.textContent=' · '+({verifying:'ตรวจไฟล์',extracting:'เตรียมเสียง',transcribing:'ถอดเสียง'}[job.step] || 'กำลังเริ่ม');item.append(phase);}
      if (job.revision !== revision()) {
        const old=document.createElement('span');old.className='badge';old.textContent='ร่างก่อนหน้า';item.append(' ',old);
      }
      if (job.result?.duration_s) {
        const duration=document.createElement('span');duration.textContent=` · ${job.result.duration_s.toFixed(1)} วินาที`;item.append(duration);
      }
      if (job.status==='failed') {
        const message=document.createElement('p');message.textContent=failures[job.error] || (job.task.kind==='transcribe'?'ถอดเสียงไม่สำเร็จ':'อ่านวิดีโอไม่สำเร็จ');item.append(message);
      }
      if (['queued','running','failed','cancelled'].includes(job.status)) {
        const button=document.createElement('button');
        const action=['failed','cancelled'].includes(job.status) ? 'retry' : 'cancel';
        button.textContent=action==='retry' ? 'ลองใหม่' : 'ยกเลิก';button.dataset.icon=action==='retry' ? 'retry' : 'x';button.disabled=job.cancel_requested && job.status==='running';
        button.onclick=async()=>{
          button.disabled=true;
          try {
            const response=await fetch('/api/v1/jobs/'+encodeURIComponent(job.id)+'/'+action,
              {method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)});
            if (!response.ok) throw Error('job_action_failed');
            await response.json();
            await connect();
          } catch (_) {connection.textContent='ไม่สำเร็จ ลองใหม่';button.disabled=false;}
        };
        item.append(' ',button);
      }
      return item;
    }));
  }
  async function connect() {
    if (closed) return;
    const attempt=++generation;clearTimeout(retryTimer);
    source?.close();connection.textContent='กำลังอ่านสถานะ';
    try {
      const response=await fetch('/api/v1/projects/'+encodeURIComponent(project)+'/jobs',
        {cache:'no-store',signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw Error('job_snapshot_failed');
      const snapshot=await response.json();const cursor=response.headers.get('x-job-cursor');
      if (!Array.isArray(snapshot) || !/^\d+$/.test(cursor || '')) throw Error('invalid_snapshot');
      if (closed || attempt!==generation) return;
      jobs.clear();replaced();for (const job of snapshot) {jobs.set(job.id,job);changed(job);}render();
      source=new EventSource('/api/v1/projects/'+encodeURIComponent(project)+'/events?after='+cursor);
      source.onopen=()=>{if (attempt===generation) connection.textContent='อัปเดตสด';};
      source.onerror=()=>{if (attempt===generation) connection.textContent='รอเชื่อมต่อ งานยังไม่หาย';};
      source.addEventListener('job',event=>{
        if (attempt!==generation) return;
        const job=JSON.parse(event.data);jobs.set(job.id,job);changed(job);render();
      });
      source.addEventListener('reset',event=>{
        if (attempt!==generation) return;
        const snapshot=JSON.parse(event.data);
        jobs.clear();replaced();for (const job of snapshot) {jobs.set(job.id,job);changed(job);}render();
      });
      source.addEventListener('storage-error',()=>{
        if (attempt!==generation) return;
        source.close();connection.textContent='อ่านสถานะไม่ได้ กำลังเชื่อมต่อใหม่';retryTimer=setTimeout(connect,3000);
      });
    } catch (_) {
      if (closed || attempt!==generation) return;
      connection.textContent='อ่านสถานะไม่ได้ กำลังเชื่อมต่อใหม่';retryTimer=setTimeout(connect,3000);
    }
  }
  render();connect();
  return {refresh:render,close:()=>{closed=true;generation++;source?.close();clearTimeout(retryTimer);cancelAnimationFrame(frame);}};
}
