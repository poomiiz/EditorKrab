//! Words the speech model keeps getting wrong, learned from this person's own fixes.
//! Port of app/spelling.py. Only whole words are swapped.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::words::split;

/// A longer change is a rewrite, not a mishearing.
pub const MAX_WORDS: usize = 3;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Correction {
    pub right: String,
    pub n: u64,
}

pub type Corrections = BTreeMap<String, Correction>;

/// {wrong: {"right", "n"}}; missing file => empty; a broken file is an error.
pub fn load(path: &Path) -> Result<Corrections, String> {
    if !path.is_file() {
        return Ok(Corrections::new());
    }
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if !value.is_object() {
        return Err(format!("{} is not a JSON object", path.display()));
    }
    serde_json::from_value(value).map_err(|e| format!("{}: {e}", path.display()))
}

fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    let dir = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let name = path
        .file_name()
        .ok_or_else(|| format!("{} has no file name", path.display()))?;
    let tmp = dir.join(format!(
        ".{}.{}.tmp",
        name.to_string_lossy(),
        uuid::Uuid::new_v4()
    ));
    std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", path.display())
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tag {
    Equal,
    Replace,
    Delete,
    Insert,
}

/// difflib.SequenceMatcher(a=a, b=b, autojunk=False) with no isjunk.
fn find_longest_match(
    a: &[String],
    b2j: &HashMap<&str, Vec<usize>>,
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    for (i, item) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut newj2len = HashMap::new();
        if let Some(js) = b2j.get(item.as_str()) {
            for &j in js {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let k = if j > 0 {
                    j2len.get(&(j - 1)).copied().unwrap_or(0)
                } else {
                    0
                } + 1;
                newj2len.insert(j, k);
                if k > bestsize {
                    besti = i + 1 - k;
                    bestj = j + 1 - k;
                    bestsize = k;
                }
            }
        }
        j2len = newj2len;
    }
    (besti, bestj, bestsize)
}

fn opcodes(a: &[String], b: &[String]) -> Vec<(Tag, usize, usize, usize, usize)> {
    let mut b2j: HashMap<&str, Vec<usize>> = HashMap::new();
    for (j, w) in b.iter().enumerate() {
        b2j.entry(w.as_str()).or_default().push(j);
    }
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut blocks = Vec::new();
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = find_longest_match(a, &b2j, alo, ahi, blo, bhi);
        if k > 0 {
            blocks.push((i, j, k));
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    blocks.sort();
    let mut merged: Vec<(usize, usize, usize)> = Vec::new();
    for (i, j, k) in blocks {
        if let Some(last) = merged.last_mut()
            && last.0 + last.2 == i
            && last.1 + last.2 == j
        {
            last.2 += k;
            continue;
        }
        merged.push((i, j, k));
    }
    merged.push((a.len(), b.len(), 0));

    let (mut i, mut j) = (0, 0);
    let mut out = Vec::new();
    for (ai, bj, size) in merged {
        let tag = match (i < ai, j < bj) {
            (true, true) => Some(Tag::Replace),
            (true, false) => Some(Tag::Delete),
            (false, true) => Some(Tag::Insert),
            _ => None,
        };
        if let Some(t) = tag {
            out.push((t, i, ai, j, bj));
        }
        i = ai + size;
        j = bj + size;
        if size > 0 {
            out.push((Tag::Equal, ai, i, bj, j));
        }
    }
    out
}

/// Keep the words changed between a heard line and its fixed version; returns the pairs learned.
pub fn learn(path: &Path, old: &str, new: &str) -> Result<Vec<(String, String)>, String> {
    let (a, b) = (split(old)?, split(new)?);
    let mut pairs = Vec::new();
    for (tag, i1, i2, j1, j2) in opcodes(&a, &b) {
        let wrong = a[i1..i2].concat().trim().to_string();
        let right = b[j1..j2].concat().trim().to_string();
        if tag == Tag::Replace
            && !wrong.is_empty()
            && !right.is_empty()
            && wrong != right
            && (i2 - i1).max(j2 - j1) <= MAX_WORDS
        {
            pairs.push((wrong, right));
        }
    }
    if !pairs.is_empty() {
        let mut data = load(path)?;
        for (wrong, right) in &pairs {
            let n = match data.get(wrong) {
                Some(seen) if &seen.right == right => seen.n + 1,
                _ => 1,
            };
            data.insert(
                wrong.clone(),
                Correction {
                    right: right.clone(),
                    n,
                },
            );
        }
        let text = serde_json::to_string_pretty(&data).map_err(|e| e.to_string())?;
        write_atomic(path, &text)?;
    }
    Ok(pairs)
}

/// The line with every learned mishearing swapped, matched on whole words only.
pub fn fix(text: &str, data: &Corrections) -> Result<String, String> {
    if data.is_empty() {
        return Ok(text.to_string());
    }
    let words = split(text)?;
    let mut longest = 0;
    for w in data.keys() {
        longest = longest.max(split(w)?.len());
    }
    let mut out = String::new();
    let mut i = 0;
    while i < words.len() {
        let mut hit_n = 0;
        for n in (1..=longest.min(words.len() - i)).rev() {
            let key = words[i..i + n].concat();
            if let Some(hit) = data.get(key.trim())
                && !words[i].trim().is_empty()
                && !words[i + n - 1].trim().is_empty()
            {
                out.push_str(&hit.right);
                hit_n = n;
                break;
            }
        }
        if hit_n > 0 {
            i += hit_n;
        } else {
            out.push_str(&words[i]);
            i += 1;
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_dir() -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("ek-spelling-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn segmentation_report() {
        for s in ["มันน่าจะดีคัป", "มันน่าจะดีครับ", "โอเคคัป", "ไปที่กองทัพ"]
        {
            eprintln!("{s:?} -> {:?}", split(s).unwrap());
        }
    }

    #[test]
    fn hand_fixes_are_learned() {
        let dir = tmp_dir();
        let path = dir.join("corrections.json");
        let pairs = learn(&path, "มันน่าจะดีคัป", "มันน่าจะดีครับ").unwrap();
        assert_eq!(pairs, vec![("คัป".to_string(), "ครับ".to_string())]);
        let data = load(&path).unwrap();
        assert_eq!(fix("โอเคคัป", &data).unwrap(), "โอเคครับ");
        assert_eq!(fix("ไปที่กองทัพ", &data).unwrap(), "ไปที่กองทัพ");
        learn(&path, "มันน่าจะดีคัป", "มันน่าจะดีครับ").unwrap();
        assert_eq!(load(&path).unwrap()["คัป"].n, 2);
        std::fs::write(&path, "{broken").unwrap();
        assert!(load(&path).is_err());
        std::fs::write(&path, "[1]").unwrap();
        assert!(load(&path).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn missing_file_is_empty() {
        let p = tmp_dir().join("none.json");
        assert!(load(&p).unwrap().is_empty());
    }

    #[test]
    fn opcodes_match_difflib() {
        let v = |s: &str| s.chars().map(|c| c.to_string()).collect::<Vec<_>>();
        // difflib.SequenceMatcher(a="abxcd", b="abcyd", autojunk=False).get_opcodes()
        let ops = opcodes(&v("abxcd"), &v("abcyd"));
        assert_eq!(
            ops,
            vec![
                (Tag::Equal, 0, 2, 0, 2),
                (Tag::Delete, 2, 3, 2, 2),
                (Tag::Equal, 3, 4, 2, 3),
                (Tag::Insert, 4, 4, 3, 4),
                (Tag::Equal, 4, 5, 4, 5),
            ]
        );
    }
}
