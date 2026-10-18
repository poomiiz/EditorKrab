"""Manual cut/undo publication survives an actual owned child-process exit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import trim_timing_test as fixture
import host_platform
import project_lock
import video_editor
import web_ai_identity as identity
import web_ai_transaction as transaction

ROOT=Path(__file__).resolve().parents[1]


class ManualCutTransaction(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TrimTiming();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.folder=self.f.folder;self.path=str(self.folder)
        for text in self.f.draft['materials']['texts']:
            content=json.loads(text['content']);content['styles']=[{'size':15}];text['content']=json.dumps(content)
        self.f.write('draft_content.json',self.f.draft)
        self.enterContext(patch.object(host_platform,'capcut_running',return_value=False))

    def files(self):
        return {name:file.read_bytes() for name,file in transaction._files(self.folder).items()}

    def cut(self):
        return video_editor.draft_cut(video_editor.CutRange(path=self.path,start=0,end=.5))

    def undo(self):
        return video_editor.draft_uncut(video_editor.DraftPath(path=self.path))

    def crash(self,operation):
        code=r"""
import os,sys
from pathlib import Path
root,folder,operation=sys.argv[1:]
import builtins,io
blocked={str((Path(root)/'config.json').resolve()).casefold(),str((Path.home()/'.clip-kit/config.json').resolve()).casefold()}
def guarded(original):
    def opened(file,*args,**kwargs):
        if isinstance(file,(str,Path)) and str(Path(file).resolve()).casefold() in blocked:
            raise AssertionError('child private config read')
        return original(file,*args,**kwargs)
    return opened
builtins.open=guarded(builtins.open);io.open=guarded(io.open)
sys.path[:0]=[str(Path(root)/'app'),str(Path(root)/'scripts')]
import host_platform,video_editor,web_ai_transaction as tx
host_platform.capcut_running=lambda:False
copy=tx._copy
original=Path(folder).resolve()
def interrupted(source,target):
    copy(source,target)
    if target.parent.resolve()==original and target.name=='draft_content.json':
        os._exit(79)
tx._copy=interrupted
if operation=='cut':
    video_editor.draft_cut(video_editor.CutRange(path=folder,start=0,end=.5))
else:
    video_editor.draft_uncut(video_editor.DraftPath(path=folder))
raise AssertionError('publication boundary not reached')
"""
        env=dict(os.environ,CLIP_KIT_CONFIG=str(self.folder.parent/'config.json'),PYTHONUTF8='1')
        result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(ROOT),self.path,operation],
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(result.returncode,79,result.stdout+result.stderr)
        journals=[json.loads(file.read_text(encoding='utf-8')) for file in identity.state_dir('clip',self.path).glob('transactions/*/journal.json')]
        self.assertTrue(any(journal['phase']=='prepared' for journal in journals))

    def test_actual_cut_process_exit_restores_metadata_and_undo_before_next_writer(self):
        before=self.files();self.crash('cut');self.assertNotEqual(self.files(),before)
        with project_lock.writer(self.path):
            self.assertEqual(self.files(),before)
        self.assertEqual(video_editor._undo_steps(self.folder),[])
        self.assertEqual(self.cut()['undo'],1)

    def test_actual_undo_process_exit_restores_cut_state_and_keeps_undo_retryable(self):
        self.cut();before=self.files();self.crash('undo');self.assertNotEqual(self.files(),before)
        with project_lock.writer(self.path):
            self.assertEqual(self.files(),before)
        self.assertEqual(self.undo()['undo'],0)
        self.assertEqual(self.f.read('draft_content.json')['duration'],8_000_000)

    def test_external_edit_after_exit_is_preserved_and_recovery_refuses(self):
        self.crash('cut');file=self.folder/'clipkit_words.json';file.write_text('{"external":[]}',encoding='utf-8')
        before=self.files()
        with self.assertRaisesRegex(fixture.edit.VideoEditError,'clipkit_words.json'):
            with project_lock.writer(self.path):
                self.fail('writer must not enter')
        self.assertEqual(self.files(),before)

    def test_two_cuts_two_undos_ignore_empty_transaction_containers(self):
        original={name:(self.folder/name).read_bytes() for name in video_editor.UNDO_FILES if (self.folder/name).is_file()}
        self.assertEqual(self.cut()['undo'],1);self.assertEqual(self.cut()['undo'],2)
        self.assertEqual(self.undo()['undo'],1);self.assertEqual(self.undo()['undo'],0)
        for name,content in original.items():self.assertEqual((self.folder/name).read_bytes(),content)
        with self.assertRaises(video_editor.HTTPException):self.undo()

    def test_manual_cut_does_not_supersede_browser_lane_and_nests_one_transaction(self):
        answer={'clipkit_web_ai':2,'step':'clip',**identity.issue('clip',self.path)}
        active=identity.state_dir('clip',self.path)/'active.json';before=active.read_bytes()
        issue=identity.issue
        with patch.object(identity,'issue',wraps=issue) as calls:self.cut()
        self.assertEqual(calls.call_count,1);self.assertEqual(active.read_bytes(),before)
        self.assertEqual(len(list(identity.state_dir('clip',self.path).glob('transactions/*/journal.json'))),1)
        with self.assertRaises(fixture.edit.VideoEditError):identity.verify('clip',self.path,answer)

    def test_open_native_editor_refuses_cut_and_undo_without_metadata_changes(self):
        for operation in (self.cut,self.undo):
            if operation==self.undo:self.cut()
            before=self.files()
            with patch.object(host_platform,'capcut_running',return_value=True),self.assertRaises((video_editor.HTTPException,fixture.edit.VideoEditError)):
                operation()
            self.assertEqual(self.files(),before)

    def test_preview_is_read_only_and_staging_context_resets_after_failure(self):
        before=self.files()
        result=fixture.edit.trim_pauses(self.path,apply=False,ranges=[(0,.5)])
        self.assertEqual(result['result_length'],7.5);self.assertEqual(self.files(),before)
        self.assertFalse(identity.state_dir('clip',self.path).exists())
        prepared=[]
        def fail(work):
            self.assertTrue(transaction.staged(work));prepared.append(work)
            raise ValueError('stage failure')
        with self.assertRaises(ValueError):
            with project_lock.writer(self.path):transaction.manual(self.path,fail)
        self.assertFalse(transaction.staged(prepared[0]));self.assertEqual(self.files(),before)


if __name__=='__main__':unittest.main()
