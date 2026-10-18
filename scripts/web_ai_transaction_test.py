"""AI replay, partial-apply rollback and motion failure preserve the real project's bytes."""
import copy
import json
import os
import subprocess
import shutil
import sys
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trim_timing_test as fixture
import atomic
import capcut_edit
import host_platform
import kit_settings
import project_lock
import hf_build
import motion_plan
import render
import draft_quality
import video_editor
import web_ai_identity as identity
import web_ai_json as web
import web_ai_transaction as transaction


class Interrupted(BaseException):
    pass


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.TrimTiming()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder = self.f.folder
        self.path = str(self.folder)
        self.addCleanup(patch.stopall)
        config=self.folder.parent/"config.json"
        config.write_text('{"render_encoder":"libx264"}',encoding="utf-8")
        patch.object(render.video_edit.kitconfig,"_CANDIDATES",[config]).start()
        patch.object(host_platform, "capcut_running", return_value=False).start()
        self.answer = {"clipkit_web_ai": 2, "step": "clip", "project": self.folder.name,
                       "hook": [], "lines": [{"n": 1, "cue_id": "0", "items": [["normal", "keyword"]]},
                                              {"n": 2, "cue_id": "1", "items": [["next", ""]]}]}
        self.answer.update(identity.issue("clip", self.path))

    def files(self):
        return {n: f.read_bytes() for n, f in transaction._files(self.folder).items()}

    def tree(self):
        return {file.relative_to(self.folder).as_posix():file.read_bytes()
                for file in self.folder.rglob('*') if file.is_file()}

    def test_motion_preview_uses_actual_word_time_without_project_or_receipt_writes(self):
        answer=copy.deepcopy(self.answer)
        answer['motion']=[{'n':1,'template':'hook-title','params':{'key':'keyword','duration':1},
                          'anchor':{'kind':'word','cue_id':'0','word':2}}]
        before=self.tree()
        metadata={'duration':8,'width':1920,'height':1080,'fps':30}
        with patch.object(kit_settings,'make_motion') as make, patch.object(render,'probe',return_value=metadata), \
                patch.object(draft_quality,'probe',return_value=metadata):
            result=web.preview_clip(self.path,json.dumps(answer))
            self.assertEqual(web.preview_clip(self.path,json.dumps(answer)),result)
        make.assert_not_called()
        self.assertEqual(self.tree(),before)
        self.assertIsNone(transaction.receipt('clip',self.path,answer))
        self.assertEqual(result['motion_action'],'replace')
        self.assertEqual(result['motion_plan'][0]['start'],4)
        self.assertNotIn(str(self.folder),json.dumps(result))

    def test_motion_preview_resolves_anchor_after_proposed_cut(self):
        answer=copy.deepcopy(self.answer);answer['cut']=[1];answer['lines'][0]={'n':1,'cue_id':'0'}
        answer['motion']=[{'n':2,'template':'hook-title','params':{'key':'next','duration':1},
                          'anchor':{'kind':'word','cue_id':'1','word':1}}]
        before=self.tree();metadata={'duration':8,'width':1920,'height':1080,'fps':30}
        with patch.object(kit_settings,'make_motion') as make, patch.object(render,'probe',return_value=metadata), \
                patch.object(draft_quality,'probe',return_value=metadata):
            result=web.preview_clip(self.path,json.dumps(answer))
        make.assert_not_called();self.assertEqual(self.tree(),before)
        self.assertEqual(result['cut'],1)
        self.assertEqual(result['motion_plan'][0]['start'],2)

    def test_preview_reports_preserve_and_clear_without_mutating_list(self):
        before=self.tree()
        self.assertEqual(web.preview_clip(self.path,json.dumps(self.answer))['motion_action'],'preserve')
        self.assertEqual(web.preview_clip(self.path,json.dumps({**self.answer,'motion':[]}))['motion_action'],'clear')
        self.assertEqual(self.tree(),before)

    def test_local_answer_preview_keeps_file_and_packet_unconsumed(self):
        before=self.tree()
        self.assertEqual(web.preview_clip(self.path,' '),{'waiting':True})
        saved=web.answer_file('clip',self.path);payload=json.dumps(self.answer)
        saved.write_text(payload,encoding='utf-8');before=self.tree()
        result=web.preview_clip(self.path,'')
        self.assertEqual(result['answer'],payload)
        self.assertEqual(result['motion_before'],0)
        self.assertEqual(self.tree(),before)

    def test_preview_http_route_never_calls_renderer_or_changes_project(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI();app.include_router(video_editor.router);before=self.tree()
        with TestClient(app) as client, patch.object(kit_settings,'make_motion') as make:
            result=client.post('/api/video/web-ai/preview',json={'step':'clip','target':self.path,'answer':json.dumps(self.answer)})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['motion_action'],'preserve')
            bad=client.post('/api/video/web-ai/preview',json={'step':'stories','target':self.path,'answer':json.dumps(self.answer)})
            self.assertEqual(bad.status_code,400)
        make.assert_not_called();self.assertEqual(self.tree(),before)

    def test_preview_rejects_changed_source_and_retains_new_external_edit(self):
        old=web._prepare_clip
        def changed(*args,**kwargs):
            result=old(*args,**kwargs)
            (self.folder/'clipkit_hook.json').write_text('[{"text":"external edit","color":"red"}]')
            return result
        with patch.object(web,'_prepare_clip',side_effect=changed), self.assertRaisesRegex(web.VideoEditError,'เปลี่ยน'):
            web.preview_clip(self.path,json.dumps(self.answer))
        self.assertEqual(json.loads((self.folder/'clipkit_hook.json').read_text())[0]['text'],'external edit')

    def test_exact_retry_after_cut_and_new_export_returns_receipt_without_cutting_again(self):
        self.answer["cut"] = [1]
        self.answer["lines"][0] = {"n": 1, "cue_id": "0"}
        first = web.import_clip(self.path, self.answer)
        after = self.files()
        identity.issue("clip", self.path)
        second = web.import_answer("clip", self.path, json.dumps(self.answer))
        self.assertEqual(first, second)
        self.assertEqual(self.files(), after)
        changed = copy.deepcopy(self.answer)
        changed["hook"] = [{"text": "other", "color": "red"}]
        with self.assertRaisesRegex(web.VideoEditError, "นำเข้าแล้ว"):
            web.import_clip(self.path, changed)
        self.assertEqual(self.files(), after)

    def test_second_motion_failure_leaves_all_original_metadata_and_legacy_data(self):
        # Two valid, disjoint placements reach the second renderer; a duplicate now conflicts before it.
        self.answer["motion"] = [{"n": 1, "template": "hook-title"}, {"n": 2, "template": "hook-title"}]
        before = self.files()
        calls = []

        def motion(body):
            assets = kit_settings._motion_destination.get()
            self.assertTrue(assets.is_relative_to(identity.state_dir("clip", self.path)))
            calls.append(body)
            if len(calls) == 2:
                raise RuntimeError("second render failed")
            assets.mkdir(parents=True)
            asset = assets / "first.mov"
            asset.write_bytes(b"synthetic motion")
            return {"file": str(asset),"recipe":motion_plan.prepare_template(body.template,body.params,width=body.width,height=body.height)}

        with patch.object(kit_settings, "make_motion", side_effect=motion), \
                patch.object(render,"probe",return_value={"duration":8,"width":1920,"height":1080,"fps":30}), \
                patch.object(draft_quality,"probe",return_value={"duration":8,"width":1920,"height":1080,"fps":30}), \
                self.assertRaisesRegex(RuntimeError, "second render"):
            web.import_clip(self.path, self.answer)
        self.assertEqual(self.files(), before)
        self.assertIsNone(transaction.receipt("clip", self.path, self.answer))

    def test_invalid_second_motion_never_starts_first_renderer(self):
        for change in ({"placement":{"x":1.5,"y":.1,"w":.3,"h":.3}},
                       {"params":{"key":"literal","duration":.2}}, {"n":1}):
            answer = copy.deepcopy(self.answer)
            answer["motion"] = [{"n":1,"template":"hook-title"},
                                {"n":2,"template":"hook-title",**change}]
            before = self.files()
            with self.subTest(change=change), patch.object(kit_settings,"make_motion") as make, \
                    patch.object(render,"probe",return_value={"duration":8,"width":1920,"height":1080,"fps":30}), \
                    patch.object(draft_quality,"probe",return_value={"duration":8,"width":1920,"height":1080,"fps":30}), \
                    self.assertRaises((web.VideoEditError,video_editor.HTTPException)):
                web.import_clip(self.path,answer)
            make.assert_not_called()
            self.assertEqual(self.files(),before)

    def test_motion_omission_preserves_existing_list(self):
        asset=self.folder/"retained-original.mov";asset.write_bytes(b"retained original")
        old=[{"start":2,"duration":1,"file":str(asset)}]
        (self.folder/video_editor.OVERLAYS).write_text(json.dumps(old),encoding="utf-8")
        self.answer.update(identity.issue("clip",self.path))
        with patch.object(kit_settings,"make_motion") as make:
            web.import_clip(self.path,self.answer)
        make.assert_not_called()
        self.assertEqual(json.loads((self.folder/video_editor.OVERLAYS).read_text()),old)

    def test_invalid_motion_shapes_reject_without_rendering_or_writing(self):
        for value in (None, {}, [{"n":1,"template":"hook-title","anchor":"guessed"}],
                      [{"n":1,"template":"hook-title","reason":" "}],
                      [{"n":1,"template":[]}], [{"n":1,"template":"hook-title","placement":"automatic"}],
                      [{"n":1,"template":"hook-title","unknown":True}]):
            answer=copy.deepcopy(self.answer);answer["motion"]=value
            before=self.files()
            with self.subTest(value=value), patch.object(kit_settings,"make_motion") as make, self.assertRaises(web.VideoEditError):
                web.import_clip(self.path,answer)
            make.assert_not_called()
            self.assertEqual(self.files(),before)

    def test_explicit_empty_motion_clears_only_metadata_and_keeps_asset(self):
        asset=self.folder/"original.mov";asset.write_bytes(b"retained original")
        (self.folder/video_editor.OVERLAYS).write_text(json.dumps([{"start":2,"duration":1,"file":str(asset)}]))
        self.answer["motion"]=[]
        self.answer.update(identity.issue("clip",self.path))
        with patch.object(kit_settings,"make_motion") as make:
            result=web.import_clip(self.path,self.answer)
        make.assert_not_called()
        self.assertEqual(json.loads((self.folder/video_editor.OVERLAYS).read_text()),[])
        self.assertEqual(asset.read_bytes(),b"retained original")
        self.assertIn(video_editor.OVERLAYS,result["written"])

    def test_requested_motion_replaces_list_with_actual_word_anchor(self):
        asset=self.folder/"original.mov";asset.write_bytes(b"retained original")
        (self.folder/video_editor.OVERLAYS).write_text(json.dumps([{"start":2,"duration":1,"file":str(asset)}]))
        self.answer["motion"]=[{"n":1,"template":"hook-title","params":{"key":"literal","duration":1},
            "anchor":{"kind":"word","cue_id":"0","word":2},"reason":"Explain the spoken keyword"}]
        self.answer.update(identity.issue("clip",self.path))
        def make(body):
            plan=motion_plan.prepare_template(body.template,body.params,width=body.width,height=body.height,placement=body.placement)
            target=kit_settings._motion_destination.get()/"generated.mov"
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b"generated fixture")
            return {"file":str(target),"recipe":plan}
        metadata={"duration":8,"width":1920,"height":1080,"fps":30}
        with patch.object(kit_settings,"make_motion",side_effect=make), \
                patch.object(render,"probe",return_value=metadata), patch.object(draft_quality,"probe",return_value=metadata):
            web.import_clip(self.path,self.answer)
        rows=json.loads((self.folder/video_editor.OVERLAYS).read_text())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["start"],4)
        self.assertEqual(rows[0]["reason"],"Explain the spoken keyword")
        self.assertEqual(rows[0]["ownership"]["mode"],"decorate")
        self.assertEqual(asset.read_bytes(),b"retained original")

    def test_failure_at_each_real_file_replacement_restores_existing_and_absent_files(self):
        before = self.files()
        before_directories = {p.relative_to(self.folder) for p in self.folder.rglob("*")
                              if p.is_dir() and ".clipkit-web-ai" not in p.parts}
        original_copy = transaction._copy

        def prepare(work, assets):
            names = ["draft_content.json", "draft_meta_info.json", "clipkit_seal.json", "clipkit_punch.json",
                     "clipkit_words.json", "clipkit_caption.json", "clipkit_hook.json", "clipkit_changes.jsonl",
                     "clipkit_history/test.json.gz", "Timelines/test/draft_content.json"]
            for name in names:
                file = work / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"prepared fixture bytes")
            return {"prepared": True}

        for fail_at in range(1, 11):
            calls = 0

            def failed(source, target):
                nonlocal calls
                if target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                    calls += 1
                    if calls == fail_at:
                        raise OSError("disk full")
                original_copy(source, target)

            with self.subTest(replacement=fail_at), patch.object(transaction, "_copy", side_effect=failed), \
                    self.assertRaisesRegex(OSError, "disk full"):
                with project_lock.writer(self.path):
                    transaction.apply("clip", self.path, self.answer, prepare)
            self.assertEqual(before, self.files())
            self.assertEqual(before_directories, {p.relative_to(self.folder) for p in self.folder.rglob("*")
                                                 if p.is_dir() and ".clipkit-web-ai" not in p.parts})
            self.assertIsNone(transaction.receipt("clip", self.path, self.answer))

    def test_crash_after_first_apply_recovers_before_manual_mutation(self):
        original_copy = transaction._copy
        before = self.files()

        def killed(source, target):
            original_copy(source, target)
            if target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                raise Interrupted()

        with patch.object(transaction, "_copy", side_effect=killed), self.assertRaises(Interrupted):
            web.import_clip(self.path, self.answer)
        self.assertNotEqual(self.files(), before)
        with project_lock.writer(self.path):
            self.assertEqual(self.files(), before)
        result = web.import_clip(self.path, self.answer)
        self.assertEqual(result["lines"], 2)

    def test_crash_after_receipt_replays_success_even_before_finalizing_journal(self):
        write = transaction._write

        def killed(file, value):
            write(file, value)
            if file.parent.name == "receipts":
                raise Interrupted()

        with patch.object(transaction, "_write", side_effect=killed), self.assertRaises(Interrupted):
            web.import_clip(self.path, self.answer)
        after = self.files()
        replay = web.import_clip(self.path, self.answer)
        self.assertEqual(replay["lines"], 2)
        self.assertEqual(after, self.files())

    def test_error_after_durable_receipt_keeps_committed_motion_asset(self):
        write = transaction._write
        original_prepare = web._prepare_clip
        assets_created = []

        def prepare(path, answer):
            result = original_prepare(path, answer)
            assets = kit_settings._motion_destination.get()
            assets.mkdir(parents=True)
            file = assets / "motion.mov"
            file.write_bytes(b"committed fixture motion")
            assets_created.append(file)
            Path(path, "clipkit_overlays.json").write_text(json.dumps([{"file": str(file)}]), encoding="utf-8")
            return result

        def disk_error(file, value):
            write(file, value)
            if file.parent.name == "receipts":
                raise OSError("error reported after successful replacement")

        with patch.object(transaction, "_write", side_effect=disk_error), \
                patch.object(web, "_prepare_clip", side_effect=prepare):
            result = web.import_clip(self.path, self.answer)
        self.assertEqual(result["lines"], 2)
        self.assertTrue(assets_created[0].is_file())
        self.assertEqual(transaction.receipt("clip", self.path, self.answer), result)

    def test_recovery_conflict_preserves_new_manual_content(self):
        original_copy = transaction._copy

        def killed(source, target):
            original_copy(source, target)
            if target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                raise Interrupted()

        with patch.object(transaction, "_copy", side_effect=killed), self.assertRaises(Interrupted):
            web.import_clip(self.path, self.answer)
        hook = self.folder / "clipkit_hook.json"
        hook.write_text('[{"text":"human edit"}]', encoding="utf-8")
        before = self.files()
        with self.assertRaisesRegex(web.VideoEditError, "พบงานแก้ใหม่"):
            web.import_clip(self.path, self.answer)
        self.assertEqual(before, self.files())

    def test_native_change_before_receipt_is_not_reported_as_success(self):
        original_copy = transaction._copy
        changed = False

        def edited(source, target):
            nonlocal changed
            original_copy(source, target)
            if not changed and target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                target.write_bytes(b"external edit")
                changed = True

        with patch.object(transaction, "_copy", side_effect=edited), self.assertRaises(web.VideoEditError):
            web.import_clip(self.path, self.answer)
        self.assertIsNone(transaction.receipt("clip", self.path, self.answer))

    def test_capcut_opening_during_prepare_refuses_publish(self):
        before = self.files()
        with patch.object(host_platform, "capcut_running", side_effect=[False, True]), \
                self.assertRaisesRegex(web.VideoEditError, "เปิดระหว่าง"):
            web.import_clip(self.path, self.answer)
        self.assertEqual(before, self.files())

    def test_changed_project_during_prepare_refuses_apply_and_preserves_manual_change(self):
        before = self.files()
        original_prepare = web._prepare_clip

        def edited(path, answer):
            result = original_prepare(path, answer)
            self.f.write("clipkit_style.json", {"anim": "none"})
            return result

        with patch.object(web, "_prepare_clip", side_effect=edited), self.assertRaises(web.VideoEditError):
            web.import_clip(self.path, self.answer)
        expected = {**before, "clipkit_style.json": (self.folder / "clipkit_style.json").read_bytes()}
        self.assertEqual(expected, self.files())

    def test_local_answer_archive_failure_can_retry_without_reapplying(self):
        file = web.answer_file("clip", self.path)
        file.write_text(json.dumps(self.answer), encoding="utf-8")
        replace = Path.replace

        def denied(path, destination):
            if path == file:
                raise PermissionError("answer is held open")
            return replace(path, destination)

        with patch.object(Path, "replace", denied), self.assertRaises(PermissionError):
            web.import_answer("clip", self.path, "")
        after = self.files()
        self.assertTrue(file.is_file())
        result = web.import_answer("clip", self.path, "")
        self.assertEqual(result["lines"], 2)
        self.assertEqual(after, self.files())
        self.assertFalse(file.is_file())

    def test_concurrent_paste_and_local_poll_share_one_effect_and_receipt(self):
        original_prepare = web._prepare_clip
        started, release = threading.Event(), threading.Event()
        file = web.answer_file("clip", self.path)
        file.write_text(json.dumps(self.answer), encoding="utf-8")
        calls = []

        def paused(path, answer):
            calls.append(path)
            started.set()
            if not release.wait(5):
                raise RuntimeError("test writer did not release")
            return original_prepare(path, answer)

        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(web, "_prepare_clip", side_effect=paused):
            future = pool.submit(web.import_answer, "clip", self.path, json.dumps(self.answer))
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(hf_build.BuildBusy):
                    web.import_answer("clip", self.path, "")
            finally:
                release.set()
            pasted = future.result(timeout=5)
        after = self.files()
        polled = web.import_answer("clip", self.path, "")
        self.assertEqual(pasted, polled)
        self.assertEqual(after, self.files())
        self.assertEqual(len(calls), 1)

    def test_failed_packet_export_keeps_pending_answer_active_identity_and_prior_packet(self):
        web.export_clip(self.path)
        packet = web.packet_file("clip", self.path)
        active = identity.state_dir("clip", self.path) / "active.json"
        file = web.answer_file("clip", self.path)
        file.write_text('{"pending":"answer"}', encoding="utf-8")
        old_packet, old_active = packet.read_bytes(), active.read_bytes()
        replace = Path.replace

        def denied(path, destination):
            if path == file:
                raise PermissionError("answer is held open")
            return replace(path, destination)

        with patch.object(Path, "replace", denied), self.assertRaises(PermissionError):
            web.export_clip(self.path)
        self.assertEqual(packet.read_bytes(), old_packet)
        self.assertEqual(active.read_bytes(), old_active)
        self.assertEqual(file.read_text(encoding="utf-8"), '{"pending":"answer"}')

    def test_process_exit_during_apply_is_recovered_by_fresh_cli_import(self):
        config = self.folder.parent / "config.json"
        config.write_text("{}", encoding="utf-8")
        answer_file = self.folder.parent / "answer.json"
        answer_file.write_text(json.dumps(self.answer), encoding="utf-8")
        code = """
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import host_platform,web_ai_json as web,web_ai_transaction as tx
host_platform.capcut_running=lambda:False
copy=tx._copy
def killed(source,target):
    copy(source,target)
    if target.is_relative_to(Path(sys.argv[2])) and '.clipkit-web-ai' not in target.parts:
        os._exit(73)
tx._copy=killed
web.import_clip(sys.argv[2],json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')))
"""
        env = {**os.environ, "CLIP_KIT_CONFIG": str(config)}
        root = Path(__file__).resolve().parents[1]
        before = self.files()
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(root / "app"), self.path,
                                 str(answer_file)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertNotEqual(before, self.files())
        resume = """
import runpy,sys
sys.path.insert(0,sys.argv[1])
import host_platform
host_platform.capcut_running=lambda:False
sys.argv=sys.argv[2:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
        # Only the generated-project child gets this process guard; never depend on the user's app being closed.
        imported = subprocess.run([sys.executable, "-X", "utf8", "-c", resume, str(root / "app"),
                                   str(root / "scripts/web_ai.py"), "import",
                                   "clip", self.path, str(answer_file)], env=env, capture_output=True,
                                  text=True, timeout=20)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["lines"], 2)
        after = self.files()
        self.assertEqual(web.import_clip(self.path, self.answer)["lines"], 2)
        self.assertEqual(after, self.files())

    def test_copied_project_cannot_replay_original_receipt_or_recover_its_journal(self):
        web.import_clip(self.path, self.answer)
        copied = self.folder.parent / "other" / self.folder.name
        shutil.copytree(self.folder, copied)
        before = {p: p.read_bytes() for p in copied.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(web.VideoEditError, "ต้นทางอื่น"):
            web.import_clip(str(copied), self.answer)
        self.assertEqual(before, {p: p.read_bytes() for p in copied.rglob("*") if p.is_file()})
        fresh = copy.deepcopy(self.answer)
        fresh.update(identity.issue("clip", str(copied)))
        self.assertEqual(web.import_clip(str(copied), fresh)["lines"], 2)

    def test_untouched_draft_changed_during_apply_is_preserved_without_receipt(self):
        before = self.files()
        original_copy = transaction._copy
        changed = False

        def edited(source, target):
            nonlocal changed
            original_copy(source, target)
            if not changed and target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                draft = self.folder / "draft_content.json"
                draft.write_bytes(draft.read_bytes() + b" ")
                changed = True

        with patch.object(transaction, "_copy", side_effect=edited), self.assertRaises(web.VideoEditError):
            web.import_clip(self.path, self.answer)
        expected = {**before, "draft_content.json": before["draft_content.json"] + b" "}
        self.assertEqual(self.files(), expected)
        self.assertIsNone(transaction.receipt("clip", self.path, self.answer))

    def test_source_changed_during_apply_preserves_source_and_rolls_back_metadata(self):
        before = self.files()
        original_copy = transaction._copy
        changed = False

        def edited(source, target):
            nonlocal changed
            original_copy(source, target)
            if not changed and target.is_relative_to(self.folder) and ".clipkit-web-ai" not in target.parts:
                self.f.source.write_bytes(b"new external footage")
                changed = True

        with patch.object(transaction, "_copy", side_effect=edited), self.assertRaises(web.VideoEditError):
            web.import_clip(self.path, self.answer)
        self.assertEqual(self.f.source.read_bytes(), b"new external footage")
        self.assertEqual(self.files(), before)
        self.assertIsNone(transaction.receipt("clip", self.path, self.answer))


if __name__ == "__main__":
    unittest.main()
