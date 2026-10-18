"""Template trust boundaries; actual generic validation and hook markup construction."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import kit_settings
        import video_editor


class MotionTemplateTests(unittest.TestCase):
    def test_draft_templates_never_reach_menu_or_renderer(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kit_settings, "KIT", Path(folder)):
            for name, status in (("draft", "draft"), ("ready", "ready")):
                directory = Path(folder) / "motion" / name
                directory.mkdir(parents=True)
                (directory / "index.html").write_text('<script type="application/json" id="clipkit-motion">'
                    + json.dumps({"status": status, "fields": []}) + '</script>', encoding="utf-8")
            self.assertEqual(set(kit_settings.motion_templates()), {"ready"})
            with patch("tempfile.mkdtemp") as allocate, self.assertRaises(kit_settings.HTTPException):
                kit_settings.make_motion(kit_settings.MotionRequest(template="draft", name="draft", params={}))
            allocate.assert_not_called()

    def test_generic_validation_keeps_fine_duration_and_rejects_nonfinite_or_oversized_text(self):
        values, duration = video_editor.motion_params("hook-title", {"key": "literal", "duration": 2.123456}, 3)
        self.assertEqual(duration, 2.123456)
        self.assertEqual(values["duration"], 2.123456)
        for params in ({"duration": float("nan")}, {"duration": float("inf")}, {"duration": True},
                       {"duration": 121}, {"key": "x" * 501}, {"unknown": "value"}):
            with self.subTest(params=params), self.assertRaises(video_editor.VideoEditError):
                video_editor.motion_params("hook-title", params, 3)

    def test_direct_render_uses_same_validation_before_config_or_temp_allocation(self):
        for params in ({"duration": float("nan")}, {"key": "x" * 501}, {"unknown": "value"}):
            with self.subTest(params=params), patch("tempfile.mkdtemp") as allocate, \
                    patch.object(kit_settings, "_read_config") as config, self.assertRaises(kit_settings.HTTPException) as error:
                kit_settings.make_motion(kit_settings.MotionRequest(template="hook-title", name="invalid", params=params))
            self.assertEqual(error.exception.status_code, 400)
            allocate.assert_not_called()
            config.assert_not_called()

    def test_hook_uses_literal_text_in_the_actual_template_markup(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node unavailable; actual hook JavaScript construction remains unchecked")
        page = (ROOT / "motion/hook-title/index.html").read_text(encoding="utf-8")
        source = re.findall(r'<script>([\s\S]*?)</script>', page)[-1]
        source = source.split("root.appendChild(h);", 1)[0] + "root.appendChild(h);"
        payload = {"key": '<img src=x onerror="attack()">&', "sub": '</script><b>quoted"</b>', "style": "straight"}
        program = r'''
const fs=require('fs'),vm=require('vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
let result;
const element=()=>({dataset:{},style:{},setAttribute(){},appendChild(value){result=value.innerHTML}});
const nodes={root:element(),bg:element()};
vm.runInNewContext(input.source,{URLSearchParams,Intl,location:{search:''},window:{KIT:input.payload},
  document:{getElementById(id){return nodes[id]},createElement:element}});
process.stdout.write(JSON.stringify(result));
'''
        result = subprocess.run([node, "-e", program], input=json.dumps({"source": source, "payload": payload}),
                                capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
        markup = json.loads(result.stdout)
        self.assertNotIn('<img', markup)
        self.assertNotIn('</script>', markup)
        self.assertNotIn('<b>', markup)
        self.assertIn('&lt;img src=x onerror=&quot;attack()&quot;&gt;&amp;', markup)
        self.assertIn('&lt;', markup)


if __name__ == "__main__":
    unittest.main()
