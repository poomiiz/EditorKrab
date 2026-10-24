//! Stamps the build with its git commit and date so the app can say which build it is (shown in the ... menu).
use std::process::Command;

fn git(args: &[&str]) -> Option<String> {
    let out = Command::new("git").args(args).output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn main() {
    let build = match git(&["rev-parse", "--short=7", "HEAD"]) {
        Some(hash) => {
            let date = git(&["log", "-1", "--format=%cs"]).unwrap_or_default();
            // "+" = built with uncommitted changes to tracked files
            let dirty = git(&["status", "--porcelain", "--untracked-files=no"]).is_some_and(|s| !s.is_empty());
            format!("{hash}{} · {date}", if dirty { "+" } else { "" })
        }
        None => "unknown (built without git)".to_owned(),
    };
    println!("cargo:rustc-env=EDITORKRAB_BUILD={build}");
    // a new commit moves the branch file, not HEAD itself, so watch both
    if let Some(head) = git(&["rev-parse", "--git-path", "HEAD"]) {
        println!("cargo:rerun-if-changed={head}");
    }
    if let Some(branch) = git(&["symbolic-ref", "-q", "HEAD"]) {
        if let Some(path) = git(&["rev-parse", "--git-path", &branch]) {
            println!("cargo:rerun-if-changed={path}");
        }
    }
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=../design/rust-flow");
}
