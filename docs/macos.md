# Mac

The EditorKrab desktop app supports **Windows 10/11 only**. There is no Mac build and no Mac install guide. See the [README](../README.md).

The old Mac setup (`setup.sh`, `start.command`, a Python `.venv`) belonged to the Python app. That app was removed on 25 Oct 2026, so those steps no longer exist.

## What is checked on a Mac

The **tests** workflow (`.github/workflows/tests.yml`) builds and tests the Rust app on two Mac runners, `macos-15` and `macos-15-intel`:

```bash
cargo build --manifest-path rust/Cargo.toml --locked
cargo test --manifest-path rust/Cargo.toml --locked
```

It also runs the page script tests with Node.js. This shows the code compiles and the tests pass there. It does not show that the app works on a Mac: nobody has run the real app on Mac hardware, and there is no Mac package.

Results from the Python era are kept in [Mac and Update checks](test-logs/macos-support.md) as a record.
