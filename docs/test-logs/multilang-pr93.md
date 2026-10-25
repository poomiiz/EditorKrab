> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Test log: รองรับหลายภาษา (PR #93)

PR: https://github.com/poomiiz/EditorKrab/pull/93 · branch `claude/project-thread-8ikx2q`
ผู้ทำ: Claude (cloud) + Claude Remote Control บนเครื่องของผู้ดูแลโปรเจกต์ · ผู้สั่ง: โอม

## สเปคเครื่องที่ใช้ทดสอบ
- เครื่อง cloud (ทดสอบ logic เท่านั้น ไม่มี GPU และไม่มีโมเดล): Intel Xeon 2.10GHz 4 cores, RAM 15 GB, Linux, Python 3.13.16, faster-whisper 1.2.1, ctranslate2 4.8.2, pythainlp 5.4.0
- เครื่องพี่โอม (โมเดลจริง): Windows 11 10.0.26300, i9-12900H (14 คอร์ / 20 เธรด), RAM 64 GB, RTX 3070 Ti Laptop VRAM 8 GB, driver 610.88, CUDA 13.3 (cuBLAS 12.9 / cuDNN 9.24), Python 3.12.10, faster-whisper 1.2.1, ctranslate2 4.8.1, pythainlp 5.3.5 โมเดลที่โหลดคือ large-v3/cuda/int8_float16
- ใช้ทรัพยากรระหว่างรันจริง: VRAM ราว 4.0 GB (จาก 1.4 GB ตอนว่าง ขึ้นไปสูงสุด 5.4 GB) และ RAM ราว 3.7 GB

## สเปคขั้นต่ำที่แนะนำ (สำหรับถอดเสียง)
- **ขั้นต่ำ**: NVIDIA GPU VRAM 6 GB และ RAM 16 GB คิดจากที่วัดได้ว่าใช้ VRAM สูงสุด 5.4 GB และ RAM 3.7 GB บวกเผื่อสำหรับ CapCut และ Windows ถ้าเปิด local LLM พร้อมกันควรมี VRAM 8 GB (`app/video_edit.py:287-288`)
- **แนะนำ**: VRAM 8 GB ขึ้นไป (ระดับ RTX 3070 Ti) เสียงไทย 180 วิ ถอดเสร็จใน 121 วิด้วย large-v3 ถ้าใช้ large-v3-turbo เหลือ 26 วิ แต่สะกดผิดมากขึ้นเล็กน้อย (`:45-46`)
- **ไม่มี GPU**: ระบบจะใช้ medium บน CPU ช้ากว่ามาก เสียงไทย 41 วิ ใช้เวลา 233 วิ และมีช่วงที่หายไปทั้งช่วง (`:42`) ยังไม่ได้ทดสอบซ้ำในรอบนี้
- ระบบปฏิบัติการ Windows 10/11, Python 3.10+, ffmpeg, Node.js LTS, CapCut desktop (`README.md:25`) และพื้นที่ดิสก์สำหรับโมเดลประมาณ 3 GB
- UNKNOWN: ยังไม่เคยทดสอบบนการ์ด VRAM 6 GB จริง

## เครื่องมือที่ใช้ และเหตุผลที่เลือก
| เครื่องมือ | ใช้ทำอะไร | ทำไมถึงเลือก | ทดสอบอะไร / ผล |
|---|---|---|---|
| faster-whisper 1.2.1 + Whisper large-v3 (มีอยู่เดิม) | ถอดเสียงและตรวจภาษา | โมเดลเดียวรองรับเกือบ 100 ภาษา ไม่ต้องเพิ่มโมเดลใหม่ ระบบเดิมวัดไว้ว่าการถอดแบบ batched เร็วกว่าเดิม 3-10 เท่า (`app/video_edit.py:350-356`) | รันจริง 7 ไฟล์ 6 ภาษา ตรวจภาษาถูกทั้งหมด (ตารางรอบ 2 ด้านล่าง) |
| `WhisperModel.detect_language` (ส่วนหนึ่งของ faster-whisper) | ตรวจภาษาของคลิป | มีอยู่แล้วใน faster-whisper ไม่ต้องเพิ่ม dependency | **ใช้ตรง ๆ ไม่ได้** เพราะอ่านแค่ 30 วิแรก ไฟล์อังกฤษถูกตรวจเป็น nl 0.52 จึงเขียน `_detect_language` ครอบไว้ ให้เฉลี่ยจาก 5 ช่วงทั่วไฟล์ (อ่านซอร์สแล้ว และรันซ้ำได้ en) |
| pythainlp 5.4.0 (มีอยู่เดิม) | ตัดคำไทย | ไม่ได้เปลี่ยน ตอนนี้เรียกใช้เฉพาะเมื่อภาษาเป็นไทย | ไทยบน main เทียบกับ branch ตรงกันทุกตัวอักษร 18 บรรทัด |
| **BudouX 0.9.3 (ใหม่)** | หาจุดตัดบรรทัดภาษาญี่ปุ่นและจีน | Google เขียนมาเพื่อตัดบรรทัดโดยเฉพาะ, Apache-2.0, ไม่มี dependency, ขนาด 300 KB, โหลด 0.001 วิ, ตัด 1,000 ประโยคใน 0.041 วิ | stub: ช่วงหยุดกลางคำ "およ\|そ" ไม่ถูกตัดแล้ว ได้ `一本の木立が / あっておよそ正午という時刻` ส่วนการทดสอบกับคลิปจริงกำลังรันบนเครื่องพี่โอม |

ตัวเลือกที่ดูแล้วไม่เลือก (อนุมานจากลักษณะของแต่ละตัว ยังไม่ได้วัดจริง):
- fugashi/MeCab ใช้ได้แค่ภาษาญี่ปุ่น และต้องลง dictionary แยก
- jieba ใช้ได้แค่ภาษาจีน และ dictionary ใหญ่กว่า
- ทั้งสองตัวต้องเพิ่มคนละ dependency ในขณะที่ BudouX ตัวเดียวครอบคลุมทั้งญี่ปุ่นและจีน

ข้อจำกัดที่รู้: BudouX ภาษาจีนแบ่งวลีหยาบกว่าภาษาญี่ปุ่น เช่น `一|曰道二|曰天` จึงต้องรอผลจากคลิปจริงก่อน

- `b773b4e` เพิ่ม `speech_language` (th / en / ... / auto) และจำกัดให้ thai_spacing กับ english_terms ทำงานเฉพาะภาษาไทย
- `30b8064` โหมด auto เฉลี่ยผลตรวจภาษาจาก 5 ช่วงที่กระจายทั่วไฟล์ ถ้าความเชื่อมั่นต่ำกว่า 0.5 จะ raise error
- `301c53e` ตัดบรรทัดภาษาญี่ปุ่นและจีนตามวลีของ BudouX (เพิ่ม `budoux==0.9.3` ใน requirements)

## 2026-10-13 02:50Z ทดสอบในเครื่อง cloud (ไม่มีโมเดลจริง เพราะ huggingface.co ถูกบล็อก)
| test | command | result |
|---|---|---|
| smoke test ก่อนแก้ (main 2fc6245) | `python scripts/smoke_test.py` | all checks passed |
| smoke test หลังแก้ b773b4e | same | all checks passed |
| `_word_starts` อังกฤษ | stub pieces `" hello"," world",...,"n"` | `[T,T,T,T,T,F,T]` ชิ้นที่อยู่กลางคำไม่ถูกนับเป็นต้นคำ |
| `_lines` อังกฤษ | same | `['hello world', 'this is Spartan again']` |
| `_word_starts` ญี่ปุ่น | `こん/にちは/世界` | `[T,T,T]` |
| `_word_starts` ไทย | `นั่/งสมาธิ/ ดี` | `[T,F,T]` เหมือนเดิม |
| `transcribe()` ค่าเริ่มต้น th (stub) | | `Th: Hello World นะครับ` ผลเดียวกับ main |
| `transcribe()` แบบ auto โดย stub ตรวจได้ en | | `en: hello world นะ ครับ` ตัวพิมพ์คงเดิม |
| CI `tests` บน b773b4e | GitHub Actions | success (02:54:40Z) |

## 2026-10-13 ~02:58Z ทดสอบในเครื่องพี่โอม: Whisper large-v3 บน GPU, commit b773b4e
| file | mode | detected | sec | result |
|---|---|---|---|---|
| jfk.flac (11 วิ, EN) | auto | en 0.90 | 9.6 | "And so my fellow Americans," / "ask not" / "what your country can do for you," ตัวพิมพ์คงเดิม ไม่มี error |
| physicsworks.wav (203 วิ, EN) | auto | **nl 0.52 ผิด** | 13.6 | 3 บรรทัดแรกเป็นอังกฤษถูกต้อง แต่ราวครึ่งไฟล์ออกมาเป็นภาษาดัตช์ และมีข้อความที่คิดขึ้นเอง "TV Gelderland 2021" |
| physicsworks.wav | บังคับ en | en | 12.6 | อังกฤษถูกทั้งไฟล์ แปลว่าปัญหาอยู่ที่การตรวจภาษา ไม่ใช่ตัวโมเดล |
| REF3 10 (60 วิแรก, TH) | th, main vs branch | | 13.5 / 14.0 | ตรงกันทุกตัวอักษร ทั้งข้อความ เวลา และคำ (18 บรรทัด) |
| ญี่ปุ่น | | | | ยังไม่ได้ทดสอบ เพราะ Wikimedia ตอบ 429 |

ข้อสังเกต: มีบรรทัดไทยตัดกลางคำ "ทุ|กรอบ" ซึ่งเป็นแบบนี้บน main อยู่แล้ว ไม่ได้มาจาก PR นี้

## สาเหตุของ nl 0.52 และวิธีแก้ (30b8064)
`WhisperModel.detect_language` ของ faster-whisper 1.2.1 อ่านแค่ 30 วิแรกของไฟล์ และหยุดทันทีเมื่อความน่าจะเป็นเกิน 0.5 (อ่านจากซอร์สของ faster-whisper) วิธีแก้คือ `_detect_language` เฉลี่ยผลจาก 5 ช่วง 30 วิที่กระจายทั่วไฟล์ และ raise error เมื่อต่ำกว่า 0.5
- stub (ช่วงแรก nl 0.52 ช่วงอื่น en 0.95) ได้ `en` จาก 5 ช่วง
- stub (th 0.3 / en 0.3) ได้ `VideoEditError: cannot tell the spoken language (best guess th, 0.30)...`
- smoke test: all checks passed

## 2026-10-13 ~03:10Z รอบ 2 บนเครื่องพี่โอม: commit 30b8064, โหมด auto
ตรวจภาษาถูกทั้ง 7 ไฟล์ ไม่มี error · log ดิบอยู่ที่ `test-log-pr93.txt` ใน worktree บนเครื่องพี่โอม
| file | detected | sec | 2 บรรทัดแรก | ตัดบรรทัด |
|---|---|---|---|---|
| physicsworks.wav (EN, 203 วิ) | en 0.55 (5 ช่วง) | 13.2 | "Now I want to return to" / "the conservation of mechanical energy." | ถูก เป็นอังกฤษทั้งไฟล์ (รอบแรกได้ nl) |
| jfk.flac (EN, 11 วิ) | en 0.90 (1 ช่วง) | 8.3 | "And so my fellow Americans," / "ask not" | ถูก |
| ญี่ปุ่น [LibriVox kouyahijiri](https://archive.org/download/kouyahijiri_1303_librivox/kouyahijiri_01_izumi_64kb.mp3) 45 วิ | ja 0.998 | 14.6 | "だから手を触るさえ厚苦しい" / "旅の衣の袖を掲げて" | **ไม่ดี** มีบรรทัดจบกลางคำ เช่น "…という一 \| 本の木立", "およ \| そ正午" |
| จีน [LibriVox art of war](https://archive.org/download/art_of_war_chinese_1506_librivox/artofwar_01_sun_64kb.mp3) | zh 0.975 | 10.7 | "不可不察也" / "故经之以武士" | **ไม่ดี** มีบรรทัดแตกกลางวลี "曰道二曰天 \| 三曰地四" และบรรทัดสุดท้ายเป็นข้อความที่โมเดลคิดขึ้นเอง ซึ่งเป็นอาการที่ Whisper รู้กันอยู่แล้ว |
| เกาหลี [LibriVox animal meeting](https://archive.org/download/animal_meeting_1509_librivox/animalmeeting_01_ahn_64kb.mp3) | ko 1.00 | 12.7 | "천추의 빛을 잃지 아니하고" / "눈을 떠서 땅을 구워보니" | ถูก (ตัดตรงช่องว่าง) |
| รัสเซีย [LibriVox ananasi](https://archive.org/download/ananasi_2005_librivox/ananasi_01_severyanin_64kb.mp3) | ru 0.999 | 13.0 | "в шампанском" / "ананасы в шампанском" | ถูก |
| ฮินดี [LibriVox panchatantra](https://archive.org/download/panchatantra_2604_librivox/panchatantra_01_sharma_64kb.mp3) | hi 0.59 | 13.8 | "आन्मोही गुणागार" / "सुखसार ब्रह्मा" | ถูก (คะแนนต่ำเพราะมีคำสันสกฤตเยอะ) |

## 2026-10-13 ~03:45Z รอบ 3 บนเครื่องพี่โอม: commit 301c53e (BudouX) · CI `tests` ผ่าน (03:41:37Z)
| file | detected | sec | ผล |
|---|---|---|---|
| ญี่ปุ่น kouyahijiri | ja 0.998 | 11.2 | **ดีขึ้น** เดิม "およ\|そ正午" ตอนนี้เป็น "およそ正午と" และเดิม "一\|本" ตอนนี้เป็น "おうという一本の木立もない" เหลือตัดกลางคำ 1 จุด คือ と\|ぼしい ส่วน "立谷空\|おう" น่าจะเป็นเพราะ Whisper ฟังคำผิดมาตั้งแต่แรก |
| จีน art of war (ภาษาจีนโบราณ) | zh 0.975 | 9.3 | **ไม่ต่างจากเดิม** ยังเป็น "曰道二曰天 \| 三曰地四" และมีจุดใหม่ "而 \| 不危也", "时至 \| 也地者" น่าจะเป็นเพราะคลิปเป็นภาษาจีนโบราณที่ไม่มีเครื่องหมายวรรคตอน ส่วน BudouX ฝึกมากับภาษาจีนปัจจุบัน (อนุมาน) |
| ไทย REF3 10 (60 วิแรก) | th | 12.2 / 14.2 | main เทียบ branch ตรงกันทุกตัวอักษร 18 บรรทัด |


ภาษาจีนปัจจุบัน 2 คลิป (60 วิ, auto, 301c53e):
| file | detected | sec | ผล |
|---|---|---|---|
| [Lu Xun 朝花夕拾](https://archive.org/download/chao_hua_si_she_jl_librivox/chaohuasishe_01_lu_64kb.mp3) จีนปัจจุบัน มีวรรคตอน | zh 0.989 | 10.9 | 22 บรรทัด ส่วนใหญ่ตัดตรงลูกน้ำหรือที่วลีจบ ตัดกลางคำ 1 จุด "打落 \| 水狗" และมี 2 จุดที่อ่านแล้วสะดุด "痒处 \| 的时候少", "愁猫的 \| 吗" |
| [Lu Xun 狂人日记](https://archive.org/download/call_to_arms_jl_librivox/calltoarms_02_lu_64kb.mp3) บทนำเป็นภาษาจีนโบราณ ที่เหลือเป็นปัจจุบัน | zh 0.979 | 11.0 | 21 บรรทัด ช่วงภาษาโบราณตัดกลางคำหลายจุด "日记二 \| 册", "搓录一 \| 篇" ส่วนช่วงภาษาปัจจุบันเหลือ 1 จุด "全 \| 是发昏" |

## สรุป
- ภาษาที่เว้นวรรคระหว่างคำ (อังกฤษ เกาหลี รัสเซีย ฮินดี) ใช้ได้แล้ว ภาษาไทยได้ผลเหมือนเดิมทุกตัวอักษร
- ญี่ปุ่น: หลังเพิ่ม BudouX ใช้ได้ เหลือตัดกลางคำ 1 จุดใน 45 วิ
- จีนปัจจุบันที่มีวรรคตอน: ใช้ได้ เหลือตัดกลางคำราว 1 จุดต่อนาที
- **ข้อจำกัดที่รู้**: ข้อความจีนที่ไม่มีวรรคตอน โดยเฉพาะภาษาจีนโบราณ ยังตัดกลางคำบ่อย ไม่แก้ใน PR นี้ ถ้าจะแก้ต้องใช้ตัวตัดคำที่ละเอียดกว่า เช่น jieba
- ความเสี่ยง: physicsworks ได้ 0.55 และฮินดีได้ 0.59 ซึ่งใกล้เกณฑ์ 0.5 คลิปจริงบางคลิปอาจขึ้น error ให้ตั้งภาษาเอง ซึ่งเป็นแบบนี้โดยตั้งใจ ไม่ใช่การเดาเงียบ ๆ
