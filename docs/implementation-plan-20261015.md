# EditorKrab — แผนพัฒนาจากการตรวจโค้ดจริง

วันที่ตรวจ: 2026-10-15 · ฐานโค้ด: `787cd64b09619984c24265fd4c1091fa24be1d38` บน `codex/desktop-motion-evidence`

งานตรวจแผน: `064642c0-1e32-4e68-a434-f2a23147b737` · งาน pilot เดิม: `4f8c1a50-3bb6-4d03-9f37-6eafd088d5ec`
สถานะ: **ตรวจและจัดแผนแล้ว; งานพัฒนาด้านล่างยังไม่ถือว่าเสร็จหรือผ่านผู้ใช้**

เป้าหมาย: รับฟุตยาว เลือกเรื่องที่จบในตัวใกล้ระยะเวลาที่ผู้ใช้ต้องการ ตัดตามเนื้อหาและเสียงจริง ใส่ซับปกติ/คำเน้น/ซับอ่าน ภาพแทรกและ motion ที่เกี่ยวข้อง แล้วส่งออก MP4 กับร่าง CapCut ที่แก้ต่อได้ ผ่าน Browser AI, local agent หรือคนทำเอง โดยใช้ข้อมูลร่วมกันและรักษางานเดิมของผู้ใช้

## 1. สิ่งที่ต้องแก้จากแผนเดิม

- **ไม่สร้างระบบกลางใหม่หรือให้ AI เขียน `draft_content.json`**: มี Web-AI schema และชุด sidecar อยู่แล้ว ให้ AI ส่งคำตัดสินตาม schema แล้ว EditorKrab สร้าง draft เอง (`prompts/web_ai/schema.md:7–30`, `app/web_ai_json.py:191,214`)
- **ตัวเลือกความยาวมีแล้ว**: ตามเนื้อหา, 30 วินาที, 1/2/3/5 นาที พร้อมบันทึก preference; งานคือทำให้ทุกทางใช้ค่าล่าสุดและแสดงผลจริง ไม่เขียนตัวเลือกซ้ำ (`app/static/video-editor.html:600`, `app/web_ai_json.py:79–129`)
- **PyThaiNLP, word clock, แบ่งเรื่อง, ตัดเงียบ, caption, motion, B-roll และ HyperFrames มีฐานแล้ว**: แก้รอยต่อและพิสูจน์ผล ไม่เพิ่มไลบรารีเพื่อแทนการตรวจงาน
- **การตัดหลังวาง motion ยังมีข้อจำกัด**: เลื่อนรายการที่ไม่คร่อมรอยตัดได้ ลบรายการที่ถูกตัดทิ้งทั้งหมดได้ แต่คร่อมบางส่วนจะปฏิเสธให้สร้างใหม่ (`app/capcut_edit.py:797–824`)
- **quality gate ปัจจุบันตรวจโครงสร้าง** ไม่ได้ฟังเสียงหรือเปิด CapCut; ผลตรวจระบุสองอย่างนี้เป็น pending อยู่แล้ว (`app/draft_quality.py:33–110`)
- **installer และ GPU มีงานบางส่วนแล้ว**: installer source/docs ยังเป็น untracked/dirty ในเครื่อง; encoder NVENC/VideoToolbox อยู่ใน `app/render.py:83` แต่ทาง HyperFrames ที่ pipeline ใช้ไม่ได้ใช้ encoder selector นี้ (`scripts/run_clip.py:439`)
- การวัดเวลาต้องปรับ protocol ก่อน: benchmark เดิมรับคลิป 30–60 วินาทีและอธิบาย CLI pilot จึงยังใช้รับรอง flow Browser AI และคลิปประมาณ 3 นาทีไม่ได้ (`scripts/bench.py:121`, `docs/benchmark.md:17`)

## 2. ผลตรวจที่กำหนดลำดับงาน

| ระดับ | สิ่งที่พบจากโค้ด/การจำลอง | ผลต่อผู้ใช้ | งานแก้ |
|---|---|---|---|
| P0 | `capcut_build.build` ลบปลายทาง `· CapCut` ด้วย `rmtree` ก่อนสร้างใหม่ (`app/capcut_build.py:163–166`) | สั่งสร้างซ้ำอาจลบการแก้ใน CapCut ของคน | EK-01 |
| P0 | words/punch/caption ใช้ข้อความเป็น key (`app/video_edit.py:950`, `app/capcut_edit.py:891–894`, `app/web_ai_json.py:262–277`) | พูดประโยคเดิมสองครั้ง แต่คนละจังหวะ กลับใช้เวลา/คำเน้นร่วมกัน | EK-02 |
| P0 | packet ตรวจชื่อไฟล์/ชื่อโปรเจกต์ ไม่มี revision; import เขียนและตัดก่อนสร้าง motion (`app/web_ai_json.py:177–185,290–317`) | คำตอบเก่า/ซ้ำอาจใช้กับงานใหม่; ล้มเหลวกลางทางเหลืองานบางส่วน | EK-03 |
| P1 | เปลี่ยน brief แต่ job restart เฉพาะ duration changed (`app/video_editor.py:320–344`); CLI resume ใช้แค่ start/end (`scripts/run_clip.py:390–395`) | ใช้เรื่องหรือ preset/ratio เก่าต่อโดยไม่แจ้ง | EK-04 |
| P1 | stories packet ไม่มี word clock; prompt จำกัดขอบ ASR row ทั้งที่ slicer รองรับขอบคำ (`app/web_ai_json.py:116`, `prompts/web_ai/stories.md:23`, `app/video_edit.py:1100`) | AI ไม่ได้ข้อมูลละเอียดพอที่จะเลือกจบประเด็นระหว่างแถว | EK-04 |
| P1 | preflight `_pair` เฉพาะกลุ่ม merge และใช้ default look/canvas; manual phrase เขียนตรง (`app/web_ai_json.py:259–261`, `app/video_editor.py:1321–1333`) | แต่ละช่องทางรับคำตอบที่ export ไม่ได้ต่างกัน | EK-05 |
| P1 | motion ทุกชนิดซ่อน text ทุกบทบาทในช่วงของมัน (`app/render.py:26`, `app/hf_build.py:142`, `app/capcut_build.py:246–256`) | การหลบซับปัจจุบันคือซ่อนซับ ไม่ใช่จัดวางอย่างเหมาะสมตามประเภท motion | EK-07 |
| P1 | B-roll เลือกผล Pixabay แรก และวางตามต้น phrase (`app/video_editor.py:1135–1162`, `app/render.py:636–641`) | ภาพอาจไม่ตรงประเด็น/คำพูด และไม่ได้เลือกช่วงที่เหมาะจากไฟล์ | EK-06 |
| P1 | safe-zone วัดหลัง `_fit`; pipeline แค่เตือนแล้วยัง export (`app/render.py:1085–1089`, `scripts/run_clip.py:434–439`) | ผลตรวจอาจไม่สะท้อนขนาด native จริง และปัญหาไม่หยุด final export | EK-08 |
| P1 | HF export ใช้ final target และ temp directory ร่วม (`app/video_editor.py:1578–1598`) | งานล้มเหลวหรือซ้อนกันอาจกระทบไฟล์สำเร็จเดิม | EK-01 |

หลักฐานจำลองใหม่ในรอบนี้: ใช้ temporary fixture ของ `scripts/trim_timing_test.py`, สร้างซับข้อความเดียวกันที่ 2–5 และ 6–9 วินาที มี keyword offset 2 วินาที แล้วตัด 3–3.5 วินาที ผลที่ถูกคือ offset ครั้งแรก 1.5 และครั้งหลัง 2 แต่ไฟล์จริงเหลือ key เดียว offset **1.5** สำหรับทั้งสองครั้ง (`bug_reproduced=true`, exit 0) ไม่ได้แก้ฟุตหรือ draft ผู้ใช้

ผลตรวจอื่นในตารางมาจาก code trace; ไม่อ้างว่าได้ทดลองทำลายงานเก่าหรือรัน failure ทุกกรณีแล้ว

## 3. ข้อตกลงข้อมูลที่ใช้พัฒนา

1. ใช้ `draft_content.json` และ sidecar เดิมเป็นฐาน เก็บ source transcript/word times แยกจากข้อความแก้ไขและข้อความย่อที่แสดง (`show`)
2. **แต่ละการปรากฏของซับต้องมี cue ID**: ใช้ ID ของ subtitle segment ที่มีอยู่เป็นจุดเริ่มต้น แทน key ข้อความสำหรับ words/punch/caption/motion/B-roll; merge เก็บ ID หัวกลุ่มและรายการต้นทาง, split สร้างลูกพร้อมอ้างต้นทาง; cut ไม่เปลี่ยน ID ของส่วนที่เหลือ
3. version sidecar/packet เท่าที่จำเป็น; reader รองรับข้อมูลเดิมที่จับคู่ได้ชัดเจน หลังสำรองจึงแปลง; legacy ที่ข้อความซ้ำและเวลาแยกสูญหายไปแล้วต้องรายงานให้ถอดเสียง/ตรวจใหม่ ห้ามแต่งเวลาคืน
4. packet ต้องผูก `packet_id`, source identity, transcript/project revision และ preferences ที่ใช้; คำตอบต้องคืนข้อมูลอ้างอิงนี้ ไม่ส่ง absolute path, token หรือเนื้อหาอื่นที่ไม่ต้องใช้
5. ใช้ clock สองชุดที่ระบุชัด: เวลาในฟุตต้นทางและเวลาในคลิปหลังตัด; ใช้ `timeline_to_source`/กฎ retime เดิม คำตอบ AI อ้าง cue/word แล้วแอปคำนวณเวลา ไม่ให้ AI เดาเวลา
6. validation ต้องใช้ source coverage, word clock, ratio, preset, font และ safe area จริง ก่อนแก้ไฟล์; งานที่ผ่านจาก browser/local/manual ต้องมีความหมายของ timeline เท่ากัน ไม่จำเป็นต้องมี UUID native เหมือนกัน
7. ทุกขั้นที่เขียนหลายไฟล์ต้องมี recovery/backup และ serialize ต่อ project; เขียนแต่ละไฟล์แบบ atomic อย่างเดียวไม่ใช่ transaction ทั้งงาน

## 4. งานพัฒนาเป็นชุด พร้อมเกณฑ์รับงาน

### EK-01 — รักษางานเดิมเมื่อ rebuild/import/export ล้มเหลว (P0)

**ทำก่อนทุกชุดที่สร้างไฟล์จริง**

- แก้ `app/capcut_build.py:163` ให้สร้าง native draft ในโฟลเดอร์ staging ใหม่ ตรวจเสร็จจึงส่งมอบ revision ใหม่ ปลายทางเดิมต้องเก็บไว้โดยค่าเริ่มต้น ใช้ history/seal ที่มีอยู่ตรวจ drift และสำรองก่อนการแทนที่ที่ผู้ใช้เลือก
- ตรวจ CapCut process ตามเครื่องมือ host ที่มีอยู่ก่อนแก้ draft และหยุดพร้อมเหตุผลเมื่อเปิดใช้งาน ไม่ kill แอปเอง; รวม guard ในฟังก์ชันร่วมเพื่อให้ API/CLI ปลอดภัยเหมือนกัน
- `draft_hf_export` ใช้ temporary output/temp ต่อ job, normalize + probe/decode สำเร็จก่อน replace final; ล้มเหลวต้องเก็บ final เดิมและ diagnostic ไม่กวาด temp ของงานอื่น
- จุดกระทบ: `app/capcut_build.py`, `app/video_editor.py` (`/draft/prepare`, `/draft/hf-export` ภายใต้ `/api/video`), `scripts/run_clip.py`, `app/capcut_edit.py` history/seal, `app/host_platform.py` (`capcut_running` ที่มีอยู่)
- **เทสรับงาน**: มี draft ผู้ใช้แก้แล้ว → rebuild ได้ชื่อใหม่และ hash เดิมไม่เปลี่ยน; copy/font/motion/render/normalize/disk fail → ไม่มีปลายทางครึ่งงาน; export ซ้อน/กดซ้ำไม่ปะปน; CapCut เปิดอยู่ถูกปฏิเสธก่อนเขียน
- **rollback**: ลบเฉพาะ staging ที่งานนั้นเป็นเจ้าของ; restore manifest/history เดิมได้ ไม่เปลี่ยน config หรือไฟล์ต้นฉบับ

### EK-02 — แยก cue และเวลาคำต่อการปรากฏจริง (P0)

**ขึ้นกับ EK-01 สำหรับการย้ายข้อมูลที่เขียนหลายไฟล์**

- เปลี่ยน lookup ที่ใช้ข้อความร่วมกันตามข้อตกลงข้อ 3; ข้อความซ้ำต้องมี clock/คำเน้น/caption/motion อิสระ
- ตรวจ caller ทั้งชุด: `video_edit.create_capcut_draft`, `capcut_edit.transcribe_draft/merge_subtitles/trim_pauses/set_line_text/tidy_subtitles`, `render.render_draft/_pair`, `hf_build.build`, `capcut_build.build`, `web_ai_json`, phrase/caption APIs, editor timeline และ query lookup
- HF/native caption ปัจจุบันรวมช่วงด้วย `line` text (`app/hf_build.py:172–178`, `app/capcut_build.py:231–236`); เปลี่ยนเป็น cue เพื่อไม่ให้ caption ค้างข้ามประโยคอื่นระหว่างข้อความซ้ำ
- เพิ่ม compatibility fixtures ของโปรเจกต์เดิม: unique text แปลงได้; duplicate text ที่กำกวมต้องแจ้งและรักษาข้อมูลเดิม
- **เทสรับงาน**: เคสจำลอง 2–5/6–9 ข้างบนต้องได้ hit คนละเวลาถูกต้อง; แก้คำครั้งเดียวไม่เปลี่ยนอีกครั้ง; merge/split/cut/undo/reload คง cue lineage; ไม่มี caption ยาวพาดข้ามคนละ occurrence
- ขยาย `trim_timing_test.py`, `subtitle_merge_test.py`, `subtitle_timing_test.py` และ fixture import/export ไม่สร้างระบบ timeline อีกชุด

### EK-03 — Browser AI/local import ผูกกับงานล่าสุดและนำเข้าซ้ำได้อย่างปลอดภัย (P0)

**ขึ้นกับ EK-01, EK-02**

- ขยาย packet/schema เดิมตามข้อ 3; ใช้ SHA256 source ที่ระบบถอดเสียงมีอยู่ ไม่อ่านไฟล์ใหญ่ซ้ำทุก request; ตรวจ revision transcript/brief/target/style/cues ที่เกี่ยวข้อง
- `_head` ต้องแยกไฟล์ชื่อเดียวกันคนละต้นทาง; pending answer เก็บเป็น history แทนลบเมื่อ export packet ใหม่ (`app/web_ai_json.py:56–58`)
- แบ่ง import เป็น validate/prepare → staging render → apply พร้อม rollback → durable receipt; motion ตัวที่สอง fail หรือ restart ระหว่าง apply ต้อง recover ได้
- serialize ต่อ project; ใช้ receipt+answer hash กัน paste ซ้ำ/local poll/API ซ้อน; packet เดิม+คำตอบเดิมคืนผลเดิม ส่วนคำตอบเปลี่ยนบน revision เก่าต้องให้ export ใหม่
- จุดกระทบ: `app/web_ai_json.py`, `scripts/web_ai.py`, API `/web-ai/export`, `/web-ai/import` ภายใต้ `/api/video`, local-answer poll, `app/static/editor.html`, `app/static/video-editor.html`, `prompts/web_ai/*`, `skills/make-clip/SKILL.md`
- **เทสรับงาน**: stale source/transcript/preset/target, ชื่อไฟล์ชนกัน, duplicate paste+poll, failure หลังตัด/merge/หนึ่ง motion, restart แล้ว retry; unsuccessful import ต้องไม่เหลืองานบางส่วน และ successful import เกิดครั้งเดียว
- legacy v1 ที่ขาด identity ยังเปิดอ่านได้ แต่ห้ามใช้แก้งานอัตโนมัติโดยถือว่าใหม่; ให้สร้าง packet รุ่นปัจจุบันพร้อมข้อความอธิบายตรงจุด

### EK-04 — เลือกเรื่องตามเนื้อหาและระยะเวลาที่ต้องการ ทุกช่องทาง (P1)

**ขึ้นกับ EK-02, EK-03; ต่อยอด duration control เดิม**

- story job/cache ต้องขึ้นกับ source+transcript+brief+target; เปลี่ยน brief อย่างเดียวหรือเลือกตามเนื้อหาแล้วห้ามคืนผลเดิมเงียบ ๆ
- stories packet ส่ง word boundaries ที่เลือกได้ และ source rows/cue IDs; prompt เลือกขอบความหมายที่เป็นขอบคำจริง ไม่ยึด ASR transport row
- ใช้ `validate_stories` ต่อ: finite/bounded/เรียง/ไม่ทับ/มีเสียง/ไม่ตัดกลางคำ; เพิ่มแสดงเหตุผลแต่ละเรื่อง จุดเริ่ม–จบ และ actual duration เทียบ target
- ความยาวเป็นความต้องการโดยประมาณ ไม่มี hard cutoff หรือเติมเนื้อหาให้ครบ; ถ้าต่างมากให้แสดงให้คนตรวจ เหตุผลที่ยาว/สั้นต้องอ่านได้ ไม่ auto-accept
- เพิ่มแก้/เพิ่ม/ลบช่วงเรื่องในรายการเดิมผ่าน validator เดียว ให้ manual flow จบได้; ยังไม่เพิ่ม multi-range montage ภายในเรื่องในรอบนี้
- CLI resume เปลี่ยนจาก key start/end อย่างเดียวเป็น manifest source/story/options revision; เปลี่ยน shape/look/preset แล้วสร้าง revision ใหม่หรือแจ้ง rebuild อย่างชัดเจน
- จุดกระทบ: `app/video_editor.py:320–344`, `app/web_ai_json.py:79–129`, `app/video_edit.py:1100–1201`, `scripts/run_clip.py:133–165,390–395`, story UI, `prompts/story_split.md`, `prompts/web_ai/stories.md`
- **เทสรับงาน**: natural/30s/1/2/3/5min roundtrip; เปลี่ยน brief; แถว ASR คร่อมสองเรื่อง; เปิดประโยคกลางคำถูกปฏิเสธ; project resume ต่าง preset/ratio ไม่ใช้ร่างเก่า; คนตรวจว่าเปิดรู้เรื่อง มีประเด็นหลัก และจบโดยไม่ต้องพึ่งเรื่องก่อนหน้า
- **หลักฐาน EK-04**: [ชุดตรวจสัญญาข้อมูลและ UI](test-logs/ek04-20261015.md) ผ่านอัตโนมัติ 14 โปรแกรม; ยังรอคนตรวจความครบประเด็นและคลิปจริง จึงอยู่ระหว่าง review

### EK-05 — ซับสามบทบาทตรงเสียงและขนาดคงที่ตาม ratio (P1)

**ขึ้นกับ EK-02, EK-03; ใช้ผล EK-04 เป็นข้อมูลนำร่อง**

- ใช้ validator ร่วมกับ browser/local/manual ทุกครั้ง รวมรายการไม่ merge; ตรวจ exact source coverage, options/look/color, clock, cue, font และ canvas จริงก่อนเขียน
- ตัดความหมายจาก source text+words; lead ค้างถึงข้อความใหม่เริ่ม, punch เข้าตรงคำและค้างคู่กัน; ไม่แบ่งเวลาตามจำนวนตัวอักษรสำหรับงานที่อ้างว่าซิงก์
- caption อ่าน/แปลแยกจาก lead+punch และกำหนดภาษาใน preset; ไม่แปลโดยเดาเมื่อข้อมูลไม่ครบ; สามบทบาทใช้ cue เดียวกันแต่มี display wording ของตนเอง
- ใช้ fixed learned layout จาก reference เป็น preset ทดสอบ; ตรวจ normal/punch/caption/hold/color-stack/second ครบทั้ง FFmpeg/HF/CapCut/`motion/sentence-pair` ไม่ใช่ตรวจเฉพาะ pair ธรรมดา
- overflow ให้ส่งกลับ cue ที่ต้องย่อ `show`/แบ่งความหมาย โดยรักษา source text และเวลาคำ; ไม่ลดขนาดเฉพาะบรรทัดใน fixed preset; legacy preset ไม่เปลี่ยนพฤติกรรมเงียบ ๆ
- คำแก้เฉพาะโปรเจกต์เป็นค่าเริ่มต้น; การเรียนรู้คำแทนทั้งเครื่องต้องแยกให้ชัด (`app/spelling.py`, `capcut_edit.set_line_text`) เพื่อไม่ให้ชื่อเฉพาะจากงานหนึ่งเปลี่ยนอีกงาน
- **หลักฐาน EK-05**: [validator/clock/layout และการกู้คืน](test-logs/ek05-20261015.md) ผ่านโปรแกรมตรวจ 25 ชุด พร้อม CPU ASS/HyperFrames และ Playwright ฟอร์มสมมติ; ยังรอฟังเสียงจริง เปิด CapCut และคนตรวจคลิปนำร่อง จึงอยู่ระหว่าง review
- จุดกระทบ: `app/render.py:243–305,325–370,577–660`, `app/hf_build.py:137–209`, `app/capcut_build.py:198–257`, `app/video_editor.py:1321`, preset learner/style lab, packet layout metadata
- **เทสรับงาน**: ไทยสระ/วรรณยุกต์, ไทยปนอังกฤษ, ชื่อ/ตัวเลข, ข้อความซ้ำ, ข้อความยาว, คำเน้นติดท้าย/ต้น, pause, รอยตัด; 9:16/16:9/1:1 ต้องไม่ล้นหรือเปลี่ยน size/center ราย phrase ใน fixed preset; ไม่มี word clock ต้องแจ้ง ไม่อ้าง sync
- ตัวเลขตรวจเริ่มต้นสำหรับ fixture: onset/offset หลังแปลง clock คลาดไม่เกิน 1 frame ของ output; เสียงจริงให้คน mark anchor ต้น/กลาง/ท้ายและรอยตัด ดู/ฟังทุก cue ที่ผิดหรือคลุมเครือ ไม่ใช้ ASR เทียบ ASR เป็นคำตัดสินสุดท้าย

### EK-06 — ภาพแทรกตรงเนื้อหาและคงต้นฉบับ (P1)

**ขึ้นกับ EK-02, EK-04, EK-05**

- นำ `scripts/run_clip.py:216–313` ซึ่งมี contact sheet + browser insert JSON matcher อยู่แล้วมาใช้ใน UI/packet เดิม; ไม่สร้าง matcher อิสระอีกตัว
- อ้าง cue/word พร้อมเหตุผลและ asset ID; รองรับเลือกภาพผู้ใช้หรือ stock แล้ว preview ก่อนใช้ แสดงที่มา/ข้อจำกัด asset; ไม่ถือว่าผลค้นหาแรกคือภาพที่ถูก
- ขยาย `BrollItem` เท่าที่ต้องใช้: source offset/duration, anchor ที่ต้น phrase หรือ keyword, crop/focus และ source reference; validate จริงกับความยาวไฟล์
- ใช้ copy/cache ที่ตรวจ identity แทนย้ายต้นฉบับผู้ใช้; `_local` ปัจจุบันอิง basename+size (`app/hf_build.py:20–29`) ต้องกันไฟล์คนละที่ชื่อ/ขนาดเดียวกันใช้ asset เก่า
- ส่งค่าเดียวกันถึง `render`, `hf_build`, `capcut_build`; เสียงพูดหลักต้องอยู่ครบ; การตัดที่คร่อม insert ให้ regenerate/แจ้งอย่างชัดเจนผ่านกฎร่วม
- **เทสรับงาน**: ไฟล์ชื่อซ้ำ, missing/short media, ภาพนิ่ง/วิดีโอ, crop แต่ละ ratio, asset offset ไม่เกินไฟล์, ตัดก่อน/กลาง insert, undo; คนตรวจว่าภาพช่วยประเด็นและเข้าจังหวะคำจริง
- **หลักฐาน EK-06**: [contract/ภาพแทรก/การกู้คืนและ Playwright](test-logs/ek06-20261015.md) ผ่านโปรแกรมตรวจ 32 ชุด พร้อม CPU export 3 ratios และส่งออก HyperFrames ผ่านปุ่มแอปด้วยสื่อจำลอง; ยังรอคนตรวจเนื้อหาจริงและ native CapCut จึงอยู่ระหว่าง review

### EK-07 — HyperFrames motion ตามเนื้อหาและจัดวางร่วมกับซับ (P1)

**ขึ้นกับ EK-02, EK-05, EK-06**

- ให้ Browser AI/local/manual ส่งแผน motion ใน contract เดิม: cue/word anchor, template, params, เหตุผลจากเนื้อหา และบทบาทที่ต้องการแทนหรือประกอบ; AI ไม่ต้องส่ง HTML หรือเรียก local tool
- ใช้ template ที่มีและตรวจแล้วก่อน เช่น sentence-pair, stat-number, hook-title; ไม่เพิ่มอัตราส่วน motion แบบบังคับทุก N วินาที/ทุกเรื่อง
- ระบุ ownership ต่อบทบาท: sentence-pair อาจแทน lead/punch ของ cue นั้น แต่ decoration/stat ไม่ควรซ่อน caption ทุกช่วงโดยอัตโนมัติ; full-screen card ต้องประกาศชัดและมี caption policy
- เพิ่ม bounds/placement ที่แอปตรวจเองตาม ratio, subtitle boxes และเขต subject; ใช้ข้อมูล face/focus ที่มีเป็นจุดเริ่ม ไม่อ้างว่าจุดกึ่งกลางใบหน้าจาก `find_focus` เป็น face tracking ทุก frame
- เริ่มจากตำแหน่งที่กำหนดและพื้นที่ห้ามทับที่คนแก้ได้; เมื่อไม่มีตำแหน่งปลอดภัยให้คืน conflict พร้อมเวลาและหยุด apply รายการนั้น ไม่ซ่อนซับ/ย้ายตำแหน่งสุ่มเงียบ ๆ; เก็บ face-occlusion review เป็น human gate จนมี detector ที่พิสูจน์แล้ว
- render/preview MP4 ใช้ bounds/ownership เดียวกับ native overlay; ตัดคร่อม motion ที่ render แล้วให้สร้างใหม่จาก cue+params หลังตัด ไม่ย่อ MOV ตามใจจนเสียงกับ animation เพี้ยน
- จุดกระทบ: `web_ai_json` motion validation, `video_editor.draft_overlay_add`, `app/render.py:26,636`, `app/hf_build.py`, `app/capcut_build.py:246`, `motion/*/index.html` metadata, `scripts/motion_handoff_test.py`, prompts/schema
- **เทสรับงาน**: เลข/รายการ/คำสำคัญ/ช่วงไม่ควรมี motion, ทั้งสาม ratio, lead+punch+caption พร้อม insert, ขอบตอน scale เข้า/ออก, motion ชนกัน, template ขาด; คนดูว่าเหมาะกับเนื้อหา ไม่บังหน้าและไม่แย่งความสำคัญของคำพูด
- motion ที่เป็น MOV ใน CapCut เป็นสื่อวางบนแทร็ก; ไม่เรียกว่า native motion ที่แก้ทุกตัวอักษร/keyframe ได้จนกว่าจะมี conversion ผ่านการเปิดจริง; provider asset ที่เครื่องไม่มีต้องระบุ unavailable

### EK-08 — ตรวจคุณภาพก่อน final export และส่งเข้า review (P1)

**ขึ้นกับ EK-01 ถึง EK-07; เริ่มเพิ่ม structural tests คู่กับแต่ละชุดได้**

- ต่อ `draft_quality.check` เดิมด้วยรายงาน timeline/layout/content completeness ที่แยกประเภท error/warning/pending; checks ไม่แก้หรือย่อข้อความขณะวัด
- safe-zone วัด text geometry จริงรวม scale/outline/rotation/animation envelope; ตรวจ overlap ตาม role/ownership ไม่ถือว่าทุก overlap เป็นความผิด เช่น caption อยู่บนภาพแทรกได้
- final export ต้องหยุดเมื่อ media/time/font/overflow/required caption เสีย; preview เพื่อแก้ปัญหายังเปิดดูได้พร้อมสถานะ error; แยก `ready_for_review` ออกจาก `approved`
- source/proxy identity: `_proxy` ปัจจุบัน cache key อิง cuts/skin แต่ไม่ source identity (`app/hf_build.py:39`); source เปลี่ยนชื่อเดิมและช่วงตัดเดิมต้องไม่ใช้ภาพเก่า
- ผลส่งมอบต้องมี exact commit/options/source IDs, MP4 hash/probe/full decode, fonts/media resolved, native open/save/reopen/playback และ human verdict ที่ผูกกับ revision; แก้ output แล้ว approval เดิมหมดอายุ
- **เทสรับงาน**: safe-zone ที่เคยผ่านเพราะ `_fit` ต้องถูกตรวจพบ; stale proxy; clip จบกลางคำ; absent caption; export fail ยังไม่ขึ้นเสร็จ; ไม่เปิด CapCutจริงต้องแสดง pending เสมอ
- ไม่ให้ parsed JSON/คะแนนอัตโนมัติทดแทนคนฟังเรื่องหรือ CapCut acceptance

### EK-09 — ทดสอบใช้งานจริงสามช่องทางและหลายฟุต (P1)

**ขึ้นกับ EK-01 ถึง EK-08; ใช้ Playwright กับ EditorKrab จริง**

- รอบแรกใช้หนึ่ง pilot บนสำเนาฟุต รวมเรื่องจบในตัว + normal/punch/caption + insert + motion ที่เนื้อหาเหมาะ และมีช่วงเงียบที่ปลอดภัยให้ตัดจริง; หากฟุตไม่มีเงียบที่ตัดได้ให้แยก fixture ไม่อ้างผลเกินจริง
- Browser: เลือก duration/ratio/preset → download packet → อัปโหลดกับ browser AI จริงโดยไม่พิมพ์ prompt เพิ่ม → นำคำตอบกลับ → preview/แก้ → MP4+CapCut
- Local agent อ่าน packet เดียวกันและคืน answer file; manual แก้เรื่อง/คำ/เน้น/caption/insert/motion ในแอป ใช้สำเนา input เดียวกันเพื่อเทียบผล
- ทดสอบสลับทางกลางงาน, reload, process interrupted, invalid/truncated JSON, คำตอบเก่า, กดซ้ำ; ทำต่อได้โดยไม่ตัดซ้ำ/สูญคำแก้
- เมื่อ pilot ผู้ใช้ดูและผ่านแล้ว จึงรันสี่คลิปทดสอบเดิมและชุดหน้างานคอนโด ไม่ปล่อย batch ก่อน approval; สร้าง reference story list จากการตรวจฟุตเต็มเพื่อวัด missed stories/fragmented stories/overlap ไม่ตัดสินจำนวนเรื่องจากไฟล์ JSON อย่างเดียว
- เก็บสัดส่วนที่ต้องแก้ ASR, cue sync/layout defects, เรื่องผ่านครั้งแรก, จำนวนรอบแก้และเหตุผล; ไม่กำหนดจำนวนคำเน้นขั้นต่ำตายตัว
- UI desktop + 375px: ชื่อไฟล์/เรื่องยาวไม่ล้น, ปุ่มกดได้, loading/empty/error, resume; ก่อน UI implementation อ่าน ui-house rules และทำ preview ตามกฎ repo
- **เสร็จเมื่อ**: รายงานระบุ revision และผู้ตรวจ, MP4ดู/ฟังเต็ม, CapCut เปิด/เล่น/แก้ซับหนึ่งจุด/save/reopen ได้, งานต้นฉบับ hash คงเดิม, ทุกเส้นทางที่อ้างว่ารองรับมีหลักฐานจริง

### EK-10 — เวลาจนคนตรวจผ่านและขอบเขตความเป็นส่วนตัว (P1)

**ออกแบบการเก็บข้อมูลก่อน EK-09; สรุปหลังมี session จริง**

- ต่อ `TimingLog`, `cutlog`, `bench.py` เดิม: version protocol ใหม่เพื่อเก็บ workflow (browser/local/manual), target/natural, actual duration, machine/preset/ratio/cache, human active/wait/fix/retry, accepted output revision
- คง benchmark รุ่นเดิม 30–60s อ่านได้; เพิ่ม protocol สำหรับระยะเวลาที่ยืดหยุ่นโดยไม่เปลี่ยนความหมายของผลเก่า และห้ามรวมคนละ brief/ratio/target เป็นคู่เทียบ
- เก็บเวลาเริ่มตั้งแต่เลือกฟุตจนผู้ตรวจผ่าน รวมทุกครั้งที่ล้มเหลวและรอ AI; nested render ไม่บวกซ้ำกับ pipeline; ช่วง engineering debug แยกจาก workflow trial
- เปรียบเทียบ manual กับ EditorKrab ด้วย rubric และคุณภาพเดียวกันตามโครง 8 sources/2 editors/16 sessions เดิมที่ปรับ version; ถ้า pair/QC/approval ยังไม่ครบให้ UNKNOWN ไม่สร้างตัวเลขเร็วขึ้น
- regression privacy: report ไป GitHub เป็น link/draft ให้คนตรวจข้อความก่อนส่ง (`app/kit_settings.py:778`), token อยู่เครื่อง, Drive ใช้ `drive.file` และ permission check เดิม (`app/drive.py:37,165–199`); media/transcript/customer paths ไม่แนบขึ้น GitHub อัตโนมัติ
- งานนี้ไม่ขยาย Supabase เป็นที่เก็บฟุต/transcript; ทดสอบขอบเขตข้อมูลที่ส่งจริงแยกจากเอกสาร config และไม่อ้าง privacy ทั้งระบบจากการอ่านไฟล์เดียว
- **เทสรับงาน**: trial ขาด baseline/approval/มี failure ต้องไม่แสดง savings; pause/retry/wait ไม่หาย; changing revision ยกเลิก approval; report payload ไม่มีข้อมูลส่วนตัวที่ไม่ได้เลือก; restricted Drive link ต้องไม่เพิ่ม permission anyone

### EK-11 — ส่งมอบตัวติดตั้ง Windows ที่ทดสอบบนเครื่องสะอาด (P2)

**เริ่มส่งมอบหลัง EK-09 ผ่าน; ตรวจ source ที่มีอยู่ได้ก่อน**

- รับช่วง `scripts/install-windows.ps1`, `scripts/build-windows-installer.ps1`, `scripts/windows-installer.cs`, `scripts/windows_installer_test.py`, `scripts/setup.ps1` และ `docs/windows-installer.md` ที่มีอยู่ใน working tree; ตรวจ owner/diff และ commit แยก ไม่รวบ untracked ทั้งหมด
- build จาก exact commit ที่จะส่งมอบ ตรวจ hash/version; ทดสอบ Windows clean account ที่ไม่มี Python/Node/FFmpeg/โมเดล รวมชื่อ path ไทย/space, download ขาด, retry, configเดิม, permission/UAC และ shortcut
- ทดสอบตั้งค่า workspace→login→รับฟุต→pilot→export หลังติดตั้งจริง; CI ใช้ tiny model/stand-in draft folder จึงไม่แทน CapCutจริง (`.github/workflows/tests.yml:79–96`)
- เอกสารต้องระบุ online bootstrap, ขนาด download, log/recovery และสถานะ signing ตามจริง; ไม่อ้างว่าเป็น offline/signed installer
- **เสร็จเมื่อ**: binary exact SHA ที่จะปล่อยผ่าน clean-machine flow และรักษา config/งานเดิม; publish/release เป็นขั้นแยกตามสิทธิ์ ไม่รวมในงานตรวจแผนนี้

### EK-12 — เร่งความเร็วจากเส้นทางที่ใช้งานจริง และรับรอง Mac (P2)

**หลัง quality gate/pilot ผ่าน; ไม่แทรกในงานแก้ clock/layout**

- วัด stage จริง: ASR, proxy, motion, HF render, encode, normalization ก่อนเลือก bottleneck; config NVENC ใน FFmpeg ไม่ได้แปลว่า HyperFrames export เร็วขึ้น
- reuse `_video_encoder` และ test/benchmark เดิม; เทียบเวลา/ภาพ/เสียง/ขนาดไฟล์ของ outputเทียบเท่ากัน ห้าม switch encoder เงียบเมื่อใช้งานไม่ได้
- ทดสอบ VideoToolbox บน Mac จริง; MLX/CoreML ASR ยังเป็นงานใหม่ แยก spike ที่ต้องพิสูจน์ Thai transcript + word timestamps + memory + fallback error ก่อนตัดสินใจเพิ่ม dependency
- ทุก backend ต้องคืน contract transcript/word clock เดียวกัน; ถ้า backend ไม่มี word timestamps ที่ผ่านเกณฑ์ยังใช้ทำคำเน้นซิงก์ไม่ได้
- จุดกระทบ: `app/render.py:83`, `app/hf_build.py:32–62`, `app/video_editor.py:1567`, `app/video_edit.py:294–320`, `scripts/render_encoder_test.py`, `scripts/render_encoder_benchmark.py`, `docs/macos.md`
- **เสร็จเมื่อ**: ผล Windows/Mac hardware จริงแยกชัด, quality เท่าเดิม, measurable improvement ในเส้นทางที่ส่งมอบ; Mac install/export/CapCut acceptance ไม่ใช้ CIเขียวแทน

## 5. ลำดับส่งมอบและการแบ่งงาน

| รอบ | งาน | สิ่งที่ส่งมอบ | เงื่อนไขไปต่อ |
|---|---|---|---|
| A | EK-01 → EK-02 → EK-03 | รักษางานเดิม, cue identity, import กู้คืนได้ | regressions ข้อมูล/กดซ้ำ/failure ผ่าน |
| B | EK-04 + EK-05 | เรื่องตามความยาว, ซับสามบทบาทใช้ clock/layout จริงทุกทาง | synthetic/fixture matrix ผ่านและ preview อ่านได้ |
| C | EK-06 → EK-07 → EK-08 | insert+motion ตาม cue และ quality report | ไม่มี structural/layout error ที่ถูกซ่อน |
| D | EK-09 + EK-10 | pilot รับรอง → corpus + timing evidence | ผู้ตรวจผ่าน; baseline ครบจึงสรุป savings |
| E | EK-11 → EK-12 | installer exact binary → performance/Mac | clean-machine และ hardware acceptance |

ทำ code/test คู่กันทุกชุดและ PR เล็กตามชุดงาน; EK-02/03/05 ใช้ไฟล์ร่วมกันมากควรลงตามลำดับ ไม่แก้พร้อมกันคนละ schema. การเก็บเวลา EK-10 และชุด fixture ของ EK-09 เตรียมขนานได้ ส่วนการ render/benchmark ใช้คิวไม่แย่ง GPU กับงานผู้ใช้

**ชุดพัฒนาแรกที่พร้อมเริ่ม: EK-01** เป้าหมาย commit `fix: preserve existing drafts and exports during rebuild` โดยใส่ Task-Id ของงานที่ claim จริง; หลังผ่านจึงเริ่มแก้ cue identity ไม่สร้างคลิปใหม่ก่อนแก้สองความเสี่ยงนี้

## 6. คำสั่งตรวจและสิ่งที่ต้องเพิ่ม

รัน suite ที่เกี่ยวข้องกับการแก้ ไม่ต้อง rerun ทุก suite เมื่อแก้เอกสารอย่างเดียว:

```powershell
python scripts/story_target_test.py
python scripts/story_timing_test.py
python scripts/long_transcription_test.py
python scripts/trim_timing_test.py
python scripts/subtitle_merge_test.py
python scripts/subtitle_timing_test.py
python scripts/preset_from_capcut_test.py
python scripts/capcut_font_test.py
python scripts/motion_handoff_test.py
python scripts/draft_quality_test.py
python scripts/benchmark_test.py
python scripts/render_encoder_test.py
python scripts/smoke_test.py
```

เพิ่มเคส failure/recovery และ duplicate cue ใน suite เดิมตามข้อข้างบน; ใช้ temporary fixtures และ fake process/render boundary ใน unit test แล้วทดสอบ EditorKrab/Playwright/MP4/CapCut จริงเป็น acceptance อีกชั้น. ชื่อไฟล์ใหม่ที่ต้องมีจริงค่อยกำหนดเมื่อเริ่มชุดงาน ไม่สร้าง harness ซ้ำโดยไม่มีเหตุผล

## 7. หลักฐาน ข้อจำกัด และการรับช่วง

- อ่านโค้ด caller/validator/render/export และหลักฐาน `docs/test-logs/onsite-ai-pilot-20261014.md` ในรอบนี้; ทำ temporary reproduction ของ duplicate-text clock หนึ่งกรณี ไม่รัน video batch/install หรือเปลี่ยน draft
- steady v3 มีหลักฐาน decode/layout และ 22 emphasis แต่ยัง `human_approved=false`; ไม่ใช้เป็น pilot ที่ผ่านสามบทบาท+insert+semantic motion ทั้งชุด
- เก็บ customer footage/transcript/font paths และ AI answer จริงไว้ local; public evidence ใช้ source ID/hash และข้อมูลที่ตัดส่วนบุคคลแล้ว
- dirty/untracked installer, acceleration docs, motion library และ README มีมาก่อนการตรวจ ต้อง review แยกก่อนรวม; file paths ในแผนเป็น repository-relative ไม่ใช้เป็น path config ของเครื่องผู้ใช้
- งาน board เดิมยังมี review gate: `4f8c1a50-3bb6-4d03-9f37-6eafd088d5ec` (pilot), `1bb90fe3-7d7c-4593-b12a-0df07d35bc36` (shared paths/install), `5deb0144-a4b4-4e9c-bb49-d0d0cbcb55f2` (privacy). ลิงก์ plan กลับงานเดิมและสร้าง/claim รายชุดเมื่อเริ่มจริง ไม่ปิด gate เดิมหรือสร้างงานซ้ำเพราะมีเอกสารใหม่
- ก่อนเริ่มแต่ละชุด: inspect HEAD+dirty files, claim task ด้วย roster codex, เติม work_order หาก high/urgent, ระบุไฟล์/endpoint/test ตามชุด, อ่าน learnings 1512 และ 946 ที่เกี่ยวข้อง แล้วใส่ Task-Id ใน commit; dev_log หนึ่งรายการต่อ taskก่อน completed
- การตรวจแผนนี้เสร็จได้เมื่อเอกสารและ source references ตรวจแล้ว ส่วน product stability จะเสร็จเมื่อ acceptance รอบ D ผ่าน. ไม่กำหนดวันเสร็จจากการอ่านโค้ดเพียงอย่างเดียว

สิ่งที่เลื่อนออก: marketplace, cloud processing/subscription, dubbing, direct publishing, timeline engine ใหม่ และ motion library จำนวนมากก่อน pilot ผ่าน. ไม่ได้ลดขอบเขต Browser AI, manual editing, cue clock, สามซับ, insert, content motion หรือการทดสอบจริง
