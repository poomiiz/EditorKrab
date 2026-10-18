# Launch kit

Release and announcement materials for version 0.29.1. Check [Releases](https://github.com/poomiiz/EditorKrab/releases) for availability. No social or community posts have been sent as part of this preparation.

Use the [promotion drafts](promotion.md) for copy and a 30-second demo plan. Check [Releases](https://github.com/poomiiz/EditorKrab/releases) before telling users to update.

## Project description

EditorKrab is an open source tool that prepares short-video drafts from talking-head footage, with editable subtitles, covers and CapCut projects.

Windows uses the existing installer. Mac support is experimental and uses CPU transcription. Every draft needs human review. Editing-time savings are unmeasured; CapCut projects on Mac and team features still need testing with real users and devices.

## Existing assets

| Asset | Use |
|---|---|
| [logo.svg](brand/logo.svg), [logo.png](brand/logo.png) | Wordmark on a light background |
| [logo-dark.svg](brand/logo-dark.svg), [logo-dark.png](brand/logo-dark.png) | Wordmark on a dark background |
| [icon.svg](brand/icon.svg), [512 px](brand/icon-512.png), [1024 px](brand/icon-1024.png) | App icon |
| [GitHub banner](brand/github-banner.png) | Repository social preview |
| [Promo](brand/promo.mp4), [GIF preview](brand/promo.gif) | 33-second workflow illustration, silent with English captions |
| [Voice-over script](brand/promo-voiceover.md) | Matching lines and timings for recording |

The promo illustrates draft preparation, review and export. It labels Mac setup experimental and makes no measured speed claim. Use the separate [demo plan](promotion.md#30-second-demo-plan) to record the real workflow with consented footage.

Editable SVG sources are included. The existing palette uses navy, coral and off-white. The Windows icon is [editorkrab.ico](../app/static/editorkrab.ico).

The plugin ID `clip-kit` and existing `clipkit` file-format names remain unchanged for compatibility.

## Release and announcement checks

| Check | Required evidence | Current status |
|---|---|---|
| Release delivery | Published tag/Release at the intended commit; Update verified in a disposable clone | [v0.29.1](https://github.com/poomiiz/EditorKrab/releases/tag/v0.29.1) published at `21c5f81`; [Update from v0.29.0](https://github.com/poomiiz/EditorKrab/actions/runs/38389874905) passed on Windows, Mac Intel and Apple Silicon |
| Software checks | CI at the release commit and required original security hotfix ancestry | [CI at `21c5f81`](https://github.com/poomiiz/EditorKrab/actions/runs/38389017829) passed; all 11 commits in the [release inventory](release-inventory.json) are ancestors of that exact release commit |
| Windows editing | A real source, exported pilot and the quality checklist | Record the actual result; software tests alone are insufficient |
| Mac editing | Installation, transcription, export and CapCut review on intended Mac devices | Experimental; still needs testing on real devices |
| Timing claim | Completed human benchmark with CSV and trial-scoped log | UNKNOWN; no real percentage available |
| Team service | Real Google sign-in, team permissions and two-device synchronization | Still needs testing with real accounts and devices |
| Public assets | Consent for demo footage, no private data, third-party attribution reviewed | Existing brand art and revised illustration reviewed; choose consented footage for the recorded demo |

When publishing an early access release, state the unresolved checks plainly. Before calling a release stable, try the editing, installation and team service with real users and devices.

## Prepare the announcement

1. Use consented footage or a purpose-made sample. Keep client names, private paths and account details out of the capture.
2. Record the steps in the [demo shot list](promotion.md#30-second-demo-plan). Show the draft review before the final MP4.
3. Use the English or Thai copy for the chosen channel after verifying the release availability.
4. Link to the repository, installation guide and known limitations.
5. Upload the banner as GitHub's social preview and post the announcement only when the owner is ready to publish.

Copy, the workflow illustration, shot list and asset references are prepared here. A recorded demo, social posts and paid media generation are separate actions.
