"""Project folders in the Rust UI: create, missing-file warning, delete footage, delete project.

Deleting sends folders to the real Recycle Bin, so everything lives in a throwaway temp folder.
"""
import json
import os
import tempfile
import time
from pathlib import Path
from rust_jobs_test import launch, port, ready, stop

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # CI images have no browser driver; run this on a desktop
    print("SKIP project folders browser test: playwright not installed")
    raise SystemExit(0)


def main():
    with tempfile.TemporaryDirectory(prefix="editorkrab-projects-ui-") as temporary:
        root = Path(temporary)
        os.environ["EDITORKRAB_PROJECTS_DIR"] = str(root / "projects")
        original = root / "footage" / "clip.mp4"
        original.parent.mkdir()
        original.write_bytes(b"not really a video")
        number = port()
        base = f"http://127.0.0.1:{number}"
        process = launch(root / "state.sqlite", number)
        try:
            ready(process, base)
            checks = run(base, root, original)
        finally:
            stop(process)
        assert original.read_bytes() == b"not really a video", "original video was touched"
    print(json.dumps({"checks": checks + ["original video untouched"]}, ensure_ascii=False))


def run(base, root, original):
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")  # the launcher already needs Edge
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base + "/")
        page.locator("#projectName").fill("รีวิว: ตอน 1")
        page.get_by_role("button", name="สร้างโปรเจกต์").click()
        page.wait_for_url("**/?project=*")
        project = page.url.split("project=")[1]
        folder = root / "projects" / "รีวิว ตอน 1"
        assert (folder / "project.editorkrab").is_file(), "project file not written"
        checks.append("create makes a named folder with its project file")

        # a linked video that has since moved away
        draft = page.request.get(f"{base}/api/v1/drafts/{project}").json()
        document = {**draft["document"], "files": [{
            "source_id": "moved", "name": "moved.mp4", "size": 5, "modified_ns": "1",
            "path": str(root / "gone" / "moved.mp4")}]}
        put = page.request.put(f"{base}/api/v1/drafts/{project}", data={
            "operation_id": "fixture", "expected_revision": draft["revision"], "document": document})
        assert put.ok, put.text()
        page.evaluate("indexedDB.deleteDatabase('editorkrab-drafts-v1')")
        page.reload()
        page.get_by_text("ไฟล์หาย").wait_for()
        assert page.get_by_role("button", name="หาที่อยู่ใหม่ของ moved.mp4").is_visible()
        checks.append("moved video shows ไฟล์หาย with a find button")

        page.get_by_role("button", name="ลบ moved.mp4 ออกจากโปรเจกต์").click()
        page.locator("#empty").wait_for(state="visible")
        deadline = time.monotonic() + 10
        while page.request.get(f"{base}/api/v1/drafts/{project}").json()["document"]["files"]:
            assert time.monotonic() < deadline, "footage removal not saved"
            time.sleep(0.1)
        checks.append("delete footage removes it from the project")

        page.locator("details.more summary").click()
        page.get_by_role("button", name="โปรเจกต์ทั้งหมด").click()
        page.wait_for_url(base + "/")
        page.get_by_role("button", name="ลบโปรเจกต์ รีวิว ตอน 1").click()
        page.locator("#projectEmpty").wait_for(state="visible")
        assert not folder.exists(), "project folder still on disk"
        checks.append("delete project sends its folder to the Recycle Bin")
        browser.close()
    return checks


if __name__ == "__main__":
    __import__("sys").stdout.reconfigure(encoding="utf-8")
    main()
