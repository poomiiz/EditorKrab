# EditorKrab - agent instructions

For any short-clip editing task, start with `skills/make-clip/SKILL.md` (the entry point), which uses `skills/clip-workflow/SKILL.md`, with the subtitle preset from `presets/` (each person makes their own), and `skills/clip-qc/SKILL.md` before calling a clip done.

- "ClipKit: ถอดสไตล์ ..." (style lab page): `skills/style-extract/SKILL.md`.
- Machine paths come from `config.json`; never hard-code a drive path.
- Close CapCut before editing a draft and back up `draft_content.json` first.
- Pilot one clip and get it watched before building the rest of a batch.
- Scripts raise on bad input; never add a silent fallback.
