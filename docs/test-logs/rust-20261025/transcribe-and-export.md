> Measured 25 Oct 2026 on build ca60fd3 (v0.30.0 minus docs). Our own runs, not guarantees.

# Transcription and 4K export on one PC

PC: Intel Core i9-12900H, NVIDIA GeForce RTX 3070 Ti Laptop GPU (8 GB), Windows 11. Footage: two iPhone 4K clips, one 946.9 s (15.8 min) and another iPhone clip used as the "next video". Language set to Thai. Each launch used a fresh project store, so the first video after launch is the cold run.

## Transcription, wait from pressing Transcribe to the full text

| Engine | Run | First video (947 s of speech) | Next video |
|---|---|---|---|
| GPU engine the app downloads (whisper.cpp, 670 MB) | 1 | 351.5 s | 147.6 s |
| GPU engine the app downloads (whisper.cpp, 670 MB) | 2 | 358.9 s | 156.9 s |
| Extra GPU engine (CT2), installed by hand, not downloaded by the app | 1 | 90.5 s | 54.7 s |
| Extra GPU engine (CT2), installed by hand, not downloaded by the app | 2 | 122.7 s | 78.9 s |

The CT2 engine is not in the download and the app does not fetch it, so a fresh install gets the first two rows. The CT2 times varied between runs (90.5 s and 122.7 s on the same file); we did not find out why.

The two engines write the same speech with different spacing, filler words and segment counts (187 and 296 segments on the long clip), so their text cannot be compared character by character. We did not score either against a reference transcript for this clip.

## Export

Two stories cut from the same 4K sources, 173 s of video in total (38 and 12 pieces), exported as 1080x1920 clips with subtitles: 76.7 s in one run (GPU decode and shrink on, libx264 encode on the CPU). One run only. Two later runs of a test build that added a zoom took 83.7 s and 86.1 s; that zoom is not in the app, so those are not results for this build.
