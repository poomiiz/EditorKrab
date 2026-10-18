# EK-01 build and export preservation

Task: `4c5c5998-c354-46ae-9ea9-84cf62d99610`

Scope: shared native build and HyperFrames export safety. This is not editorial or native-player acceptance.

## Changes

- Native builds publish a new UUID-suffixed CapCut revision from an owned staging directory. Existing native projects and source drafts remain unchanged. The shared function checks CapCut before writing and before publication, validates the source seal and generated structure, and serializes access to native template state.
- HyperFrames exports hold OS reservations for both the canonical project and final output. CLI and app processes use the same reservation files. Rendering and loudness normalization use a unique staging directory beside the final MP4. Positive audio/video, expected duration/canvas and complete FFmpeg decoding must pass before replacement.
- Failed exports retain per-job diagnostics and staged artifacts. Cleanup problems after publication return a warning rather than falsely reporting export failure.
- Proxy preparation uses temporary files and preserves earlier proxies on failure. Cache identity now includes source path, size and modification time; this is not content-hash validation for adversarial same-size/same-timestamp replacement.
- The HyperFrames launcher runs in a Windows Job Object, assigned while suspended before resuming its primary thread. Timeout and normal return terminate remaining owned workers before reservations are released. POSIX uses a separate session/process group; that branch still needs Mac/Linux execution evidence.

## Verification on Windows

Commands and observed results:

| Command | Result |
|---|---|
| `python scripts/capcut_build_safety_test.py` | 12 passed |
| `python scripts/capcut_font_test.py` | 7 passed |
| `python scripts/hf_export_safety_test.py` | 13 passed |
| `python scripts/render_process_test.py` | 5 passed |
| `python scripts/motion_handoff_test.py` | 14 passed |
| `python scripts/subtitle_timing_test.py` | 14 passed |
| `python scripts/draft_quality_test.py` | 12 passed |
| `python scripts/render_encoder_test.py` | 5 passed before final launcher integration; encoder code unchanged |
| `python scripts/smoke_test.py` | all checks passed after final launcher integration |

Native regressions first reproduced replacement of an existing delivery and absence of a running-CapCut guard. Fixtures now carry valid source ranges and canvas information; the real structural checker remains enabled.

Export failure fixtures cover render error, timeout, normalization, probing, missing audio, wrong ratio, truncated-but-decodable output, decode error, publication failure, launcher preparation and full disk while logging. Previous final bytes remain unchanged. Tests also cover duplicate project/output reservations across processes, unique staging, cleanup warnings, and failed proxy recovery.

A generated 64×96, one-second video with real audio was normalized, probed and decoded using actual FFmpeg/ffprobe before publication. The renderer boundary is substituted in this test; it is not a HyperFrames-render acceptance test.

Process tests launch only disposable Python worker trees. They verify live-parent timeout, exited-parent orphan workers, normal-exit workers, assignment failure before execution, retained stdout/stderr, and survival of an unrelated test process. The actual installed `npx --version` also completed inside the Windows Job Object (exit 0, version 10.9.3).

An additional actual HyperFrames export used generated blue footage and sine-wave audio (no customer media), through `draft_hf_export` with the contained launcher, real proxy preparation, normalization, probe, full decode and publication. Result: 2.00 s, 320×480, 30 fps, H.264/AAC stereo; the export call took 28.8 s including preparation. This is a bounded functional smoke run, not a speed or editorial benchmark. Exact private proof location is recorded in ignored `state/ek01-synthetic-proof.json`.

Windows APIs were checked against Microsoft's [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) and [ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread) contracts.

## Remaining gates

- No customer draft was edited and no new customer clip was rendered in this safety pass. Native CapCut opening/playback, representative full-resolution HyperFrames export and Mac execution remain pending.
- This change does not resolve EK-02 repeated-text clock collisions, semantic editing, three-role acceptance or measured time savings.
- Failed render directories are intentionally retained for diagnosis and may be large. Existing output files stay protected; diagnose before deleting owned failure artifacts.
