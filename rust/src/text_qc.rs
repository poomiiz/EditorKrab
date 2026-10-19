//! On-screen text proofreading from the draft's own texts: Thai/English content and
//! character sequences that render broken. Checking rendered frames needs a rendered clip.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Item {
    /// headline, super, subtitle, emphasis, english or graphic.
    pub kind: String,
    /// Caller's id for the text, returned on each finding so the line can be found again.
    #[serde(default)]
    pub id: Option<String>,
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
    /// Which source file the times belong to, when texts come from several files.
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Severity {
    Critical,
    Moderate,
    Minor,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Finding {
    pub start_ms: u64,
    pub end_ms: u64,
    pub kind: String,
    pub id: Option<String>,
    pub source: Option<String>,
    pub language: &'static str,
    pub shown: String,
    /// "content" (spelling, grammar, spacing) or "graphic" (characters that render broken).
    pub category: &'static str,
    pub problem: String,
    /// The whole text with this one problem corrected, when a correction is known.
    pub fix: Option<String>,
    pub severity: Severity,
}

const KINDS: [&str; 6] = [
    "headline", "super", "subtitle", "emphasis", "english", "graphic",
];

/// Frequent Thai misspellings and their Royal Institute forms.
const THAI_SPELLING: &[(&str, &str)] = &[
    ("อนุญาติ", "อนุญาต"),
    ("ผลัดวันประกันพรุ่ง", "ผัดวันประกันพรุ่ง"),
    ("ฟุ่มเฟื่อย", "ฟุ่มเฟือย"),
    ("สังเกตุ", "สังเกต"),
    ("ไกล้", "ใกล้"),
    ("โอกาศ", "โอกาส"),
    ("กระเพรา", "กะเพรา"),
    ("ลายเซ็นต์", "ลายเซ็น"),
    ("ศรีษะ", "ศีรษะ"),
    ("อาการณ์", "อาการ"),
    ("เอกลักษณ์ณ์", "เอกลักษณ์"),
    ("ผลลัพท์", "ผลลัพธ์"),
    ("บังเอิน", "บังเอิญ"),
    ("ทะเลสาป", "ทะเลสาบ"),
    ("เกม์", "เกม"),
    ("แก๊ซ", "แก๊ส"),
    ("คุ้กกี้", "คุกกี้"),
    ("ซีรี่ย์", "ซีรีส์"),
    ("ซีรีย์", "ซีรีส์"),
    ("อีเมล์", "อีเมล"),
    ("เวบไซต์", "เว็บไซต์"),
    ("แอพพลิเคชั่น", "แอปพลิเคชัน"),
    ("แอพ", "แอป"),
    ("คอมเม้นท์", "คอมเมนต์"),
    ("คอมเม้น", "คอมเมนต์"),
    ("โปรแกรมเมอร์ร์", "โปรแกรมเมอร์"),
    ("ไลฟ์สไตล์ล์", "ไลฟ์สไตล์"),
    ("เฟสบุ๊ค", "เฟซบุ๊ก"),
    ("เฟซบุ๊ค", "เฟซบุ๊ก"),
    ("ยูทูป", "ยูทูบ"),
    ("กราฟฟิก", "กราฟิก"),
    ("กราฟฟิค", "กราฟิก"),
    ("โน๊ต", "โน้ต"),
];

/// Brand and proper-name casing.
const BRANDS: &[&str] = &[
    "YouTube",
    "iPhone",
    "iPad",
    "LinkedIn",
    "Facebook",
    "TikTok",
    "Instagram",
    "WhatsApp",
    "PowerPoint",
    "JavaScript",
    "ChatGPT",
    "macOS",
    "iOS",
    "Google",
    "Netflix",
    "Spotify",
];

fn consonant(c: char) -> bool {
    ('\u{0E01}'..='\u{0E2E}').contains(&c)
}
/// Marks drawn above or below a consonant (upper/lower vowels, tone marks, thanthakhat, etc.).
fn above_below(c: char) -> bool {
    matches!(c, '\u{0E31}' | '\u{0E34}'..='\u{0E3A}' | '\u{0E47}'..='\u{0E4E}')
}
fn tone(c: char) -> bool {
    ('\u{0E48}'..='\u{0E4B}').contains(&c)
}
/// Name of a Thai mark, since a lone mark cannot be drawn readably in a report.
fn mark_name(c: char) -> String {
    match c {
        '\u{0E48}' => "ไม้เอก".into(),
        '\u{0E49}' => "ไม้โท".into(),
        '\u{0E4A}' => "ไม้ตรี".into(),
        '\u{0E4B}' => "ไม้จัตวา".into(),
        '\u{0E4C}' => "ทัณฑฆาต".into(),
        '\u{0E47}' => "ไม้ไต่คู้".into(),
        '\u{0E31}' => "ไม้หันอากาศ".into(),
        _ => format!("U+{:04X}", c as u32),
    }
}
fn thai(c: char) -> bool {
    ('\u{0E00}'..='\u{0E7F}').contains(&c)
}

fn language(text: &str) -> &'static str {
    let thai_letters = text.chars().filter(|c| thai(*c)).count();
    let latin = text.chars().filter(|c| c.is_ascii_alphabetic()).count();
    match (thai_letters > 0, latin > 0) {
        (true, true) => "mixed",
        (true, false) => "th",
        (false, true) => "en",
        _ => "other",
    }
}

pub fn check(items: &[Item]) -> Result<Vec<Finding>, String> {
    if items.len() > 20_000 {
        return Err("too_many_texts".into());
    }
    let mut found = Vec::new();
    for (n, item) in items.iter().enumerate() {
        if !KINDS.contains(&item.kind.as_str())
            || item.end_ms < item.start_ms
            || item.text.chars().count() > 2000
        {
            return Err(format!("invalid_text_item: {n}"));
        }
        check_item(item, &mut found)?;
    }
    consistency(items, &mut found);
    repeats(items, &mut found);
    found.sort_by(|a, b| {
        (a.start_ms, a.severity, &a.problem).cmp(&(b.start_ms, b.severity, &b.problem))
    });
    Ok(found)
}

fn check_item(item: &Item, found: &mut Vec<Finding>) -> Result<(), String> {
    let text = &item.text;
    let mut add = |category, problem: String, fix: Option<String>, severity| {
        found.push(Finding {
            start_ms: item.start_ms,
            end_ms: item.end_ms,
            kind: item.kind.clone(),
            id: item.id.clone(),
            source: item.source.clone(),
            language: language(text),
            shown: text.clone(),
            category,
            problem,
            fix,
            severity,
        })
    };
    if text.trim().is_empty() {
        add("graphic", "ข้อความว่าง".into(), None, Severity::Critical);
        return Ok(());
    }
    // Characters a font cannot draw: replacement character, boxes, control characters.
    if let Some(c) = text.chars().find(|c| {
        matches!(c, '\u{FFFD}' | '\u{25A1}' | '\u{25AF}') || (c.is_control() && *c != '\n')
    }) {
        add(
            "graphic",
            format!("ตัวอักษรแสดงผลไม่ได้ (U+{:04X})", c as u32),
            None,
            Severity::Critical,
        );
    }
    // Upper/lower marks need a consonant beneath; two tone marks or a repeated mark collide.
    let chars: Vec<char> = text.chars().collect();
    for (n, &c) in chars.iter().enumerate() {
        if !above_below(c) {
            continue;
        }
        let base = chars[..n].iter().rev().find(|p| !above_below(**p));
        if base.is_none_or(|b| !consonant(*b) && *b != 'ฤ' && *b != 'ฦ') {
            add(
                "graphic",
                format!("{} ไม่มีพยัญชนะรองรับ จะลอยผิดตำแหน่ง", mark_name(c)),
                None,
                Severity::Critical,
            );
            break;
        }
        if n > 0 && (chars[n - 1] == c || (tone(c) && tone(chars[n - 1]))) {
            add(
                "graphic",
                format!("{} ซ้อนกับ {}", mark_name(chars[n - 1]), mark_name(c)),
                None,
                Severity::Critical,
            );
            break;
        }
    }
    let mut fixed = text.clone();
    for (wrong, right) in THAI_SPELLING {
        if wrong != right && fixed.contains(wrong) {
            fixed = fixed.replace(wrong, right);
            add(
                "content",
                format!("สะกดผิด \"{wrong}\""),
                Some(text.replace(wrong, right)),
                Severity::Moderate,
            );
        }
    }
    // A word typed twice, e.g. "สวัสดีดีครับ" splits as "สวัสดี","ดี": the next word repeats the previous tail.
    let words = crate::words::split(text)?;
    for pair in words.windows(2) {
        let (a, b) = (&pair[0], &pair[1]);
        if !b.trim().is_empty() && b.chars().count() > 1 && a.ends_with(b.as_str()) {
            add(
                "content",
                format!("คำซ้ำติดกัน \"{a}{b}\" (ถ้าตั้งใจซ้ำ ใช้ ๆ)"),
                Some(text.replacen(&format!("{a}{b}"), a, 1)),
                Severity::Moderate,
            );
        }
    }
    // A space that splits one word, e.g. "ประ เทศ": without the space, a word crosses the gap.
    for triple in words.windows(3) {
        let (a, gap, b) = (&triple[0], &triple[1], &triple[2]);
        if gap == " " && a.chars().all(thai) && b.chars().all(thai) {
            let joined = crate::words::split(&format!("{a}{b}"))?;
            let mut at = 0;
            let crosses = joined.iter().any(|w| {
                let (from, to) = (at, at + w.len());
                at = to;
                from < a.len() && to > a.len() && crate::words::known(w)
            });
            if crosses {
                add(
                    "content",
                    format!("เว้นวรรคกลางคำ \"{a} {b}\""),
                    Some(text.replacen(&format!("{a} {b}"), &format!("{a}{b}"), 1)),
                    Severity::Minor,
                );
            }
        }
    }
    if text.contains("  ") {
        add("content", "เว้นวรรคซ้อน".into(), None, Severity::Minor);
    }
    if text != text.trim() {
        add(
            "content",
            "มีช่องว่างหัวหรือท้ายข้อความ".into(),
            Some(text.trim().into()),
            Severity::Minor,
        );
    }
    if text.chars().any(|c| ('๐'..='๙').contains(&c)) && text.chars().any(|c| c.is_ascii_digit())
    {
        add(
            "content",
            "ใช้เลขไทยปนเลขอารบิก".into(),
            None,
            Severity::Minor,
        );
    }
    for brand in BRANDS {
        for word in text.split(|c: char| !c.is_ascii_alphanumeric()) {
            if word.eq_ignore_ascii_case(brand) && word != *brand {
                add(
                    "content",
                    format!("ชื่อเฉพาะสะกดตัวพิมพ์ผิด \"{word}\""),
                    Some(text.replace(word, brand)),
                    Severity::Minor,
                );
            }
        }
    }
    let latin: Vec<&str> = text
        .split(|c: char| !c.is_ascii_alphabetic() && c != '\'')
        .filter(|w| !w.is_empty())
        .collect();
    for pair in latin.windows(2) {
        if pair[0].eq_ignore_ascii_case(pair[1]) && pair[0].len() > 1 {
            add(
                "content",
                format!("English word repeated \"{} {}\"", pair[0], pair[1]),
                Some(text.replacen(&format!("{} {}", pair[0], pair[1]), pair[0], 1)),
                Severity::Moderate,
            );
        }
    }
    if format!(" {text} ").contains(" i ") {
        add(
            "content",
            "English \"i\" should be \"I\"".into(),
            Some(format!(" {text} ").replace(" i ", " I ").trim().to_string()),
            Severity::Minor,
        );
    }
    if item.end_ms > item.start_ms {
        // Over 20 characters per second (marks above/below not counted) cannot be read in time.
        let chars = text
            .chars()
            .filter(|c| !c.is_whitespace() && !above_below(*c))
            .count() as u64;
        let ms = item.end_ms - item.start_ms;
        if chars * 1000 > ms * 20 {
            add(
                "graphic",
                format!(
                    "ขึ้นจอสั้นเกินอ่านทัน ({chars} ตัวอักษรใน {:.1} วินาที)",
                    ms as f64 / 1000.0
                ),
                None,
                Severity::Minor,
            );
        }
    }
    Ok(())
}

/// The same brand written two ways across headline, super and subtitle.
fn consistency(items: &[Item], found: &mut Vec<Finding>) {
    let mut seen: std::collections::BTreeMap<String, (String, &Item)> = Default::default();
    for item in items {
        for word in item.text.split(|c: char| !c.is_ascii_alphanumeric()) {
            // All-caps headlines are a style, not a spelling; compare only mixed-case names.
            let upper = word.chars().filter(|c| c.is_ascii_uppercase()).count();
            if word.len() < 3 || upper == 0 || upper == word.len() {
                continue;
            }
            let key = word.to_ascii_lowercase();
            match seen.get(&key) {
                Some((first, at)) if first != word => found.push(Finding {
                    start_ms: item.start_ms,
                    end_ms: item.end_ms,
                    kind: item.kind.clone(),
                    id: item.id.clone(),
                    source: item.source.clone(),
                    language: "en",
                    shown: item.text.clone(),
                    category: "content",
                    problem: format!(
                        "สะกดไม่ตรงกับ {} ที่ {:.1} วินาที (\"{first}\" กับ \"{word}\")",
                        at.kind,
                        at.start_ms as f64 / 1000.0
                    ),
                    fix: Some(item.text.replace(word, first)),
                    severity: Severity::Minor,
                }),
                None => {
                    seen.insert(key, (word.to_string(), item));
                }
                _ => {}
            }
        }
    }
}

/// The same subtitle 3+ times in a row: speech recognition stuck in a loop, not real speech.
fn repeats(items: &[Item], found: &mut Vec<Finding>) {
    let mut subtitles: Vec<&Item> = items.iter().filter(|i| i.kind == "subtitle").collect();
    subtitles.sort_by(|a, b| (&a.source, a.start_ms).cmp(&(&b.source, b.start_ms)));
    let same = |a: &Item, b: &Item| a.source == b.source && a.text.trim() == b.text.trim();
    let mut start = 0;
    while start < subtitles.len() {
        let mut end = start + 1;
        while end < subtitles.len() && same(subtitles[start], subtitles[end]) {
            end += 1;
        }
        if end - start >= 3 {
            // The first one may be real speech; flag the copies after it.
            for item in &subtitles[start + 1..end] {
                found.push(Finding {
                    start_ms: item.start_ms,
                    end_ms: item.end_ms,
                    kind: item.kind.clone(),
                    id: item.id.clone(),
                    source: item.source.clone(),
                    language: language(&item.text),
                    shown: item.text.clone(),
                    category: "content",
                    problem: format!(
                        "ซับเดียวกันซ้ำ {} ท่อนติดกัน (ถอดเสียงวนซ้ำ ฟังแล้วแก้หรือลบ)",
                        end - start
                    ),
                    fix: None,
                    severity: Severity::Moderate,
                });
            }
        }
        start = end;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn item(kind: &str, text: &str, start_ms: u64, end_ms: u64) -> Item {
        Item {
            kind: kind.into(),
            text: text.into(),
            start_ms,
            end_ms,
            source: None,
            id: None,
        }
    }
    fn problems(items: &[Item]) -> Vec<String> {
        check(items)
            .unwrap()
            .into_iter()
            .map(|f| f.problem)
            .collect()
    }
    #[test]
    fn finds_spec_examples() {
        let p = problems(&[item("subtitle", "สวัสดีดีครับ ขออนุญาติ ไกล้แล้ว", 0, 5000)]);
        assert!(p.iter().any(|s| s.contains("อนุญาติ")), "{p:?}");
        assert!(p.iter().any(|s| s.contains("ไกล้")), "{p:?}");
        assert!(p.iter().any(|s| s.contains("คำซ้ำ")), "{p:?}");
        let p = problems(&[item("headline", "ประ เทศไทย", 0, 5000)]);
        assert!(p.iter().any(|s| s.contains("เว้นวรรคกลางคำ")), "{p:?}");
    }
    #[test]
    fn finds_rendering_problems() {
        let p = problems(&[item("super", "ก\u{0E48}\u{0E48}า", 0, 5000)]);
        assert!(p.iter().any(|s| s.contains("ซ้อนกับ")), "{p:?}");
        let p = problems(&[item("super", " \u{0E49}ข่าว", 0, 5000)]);
        assert!(p.iter().any(|s| s.contains("ไม่มีพยัญชนะ")), "{p:?}");
        let p = problems(&[item("super", "ข่าว\u{FFFD}", 0, 5000)]);
        assert!(p.iter().any(|s| s.contains("U+FFFD")), "{p:?}");
    }
    #[test]
    fn english_and_consistency() {
        let p = problems(&[
            item("headline", "ดู Youtube", 0, 5000),
            item("subtitle", "the the iPhone", 6000, 9000),
            item("super", "iphone ใหม่", 9000, 12000),
        ]);
        assert!(p.iter().any(|s| s.contains("Youtube")), "{p:?}");
        assert!(p.iter().any(|s| s.contains("repeated")), "{p:?}");
        assert!(p.iter().any(|s| s.contains("iphone")), "{p:?}");
    }
    #[test]
    fn clean_text_has_no_findings() {
        assert!(problems(&[item("subtitle", "วันนี้เรามาคุยเรื่องกาแฟกัน", 0, 4000)]).is_empty());
        assert!(problems(&[item("headline", "กินข้าวที่ร้านใกล้บ้าน", 0, 4000)]).is_empty());
    }
    #[test]
    fn no_noise_on_caps_or_abbreviations() {
        assert!(
            problems(&[
                item("headline", "THE BEST", 0, 5000),
                item("subtitle", "The best i.e. this", 0, 5000)
            ])
            .is_empty()
        );
    }
    #[test]
    fn flags_subtitle_loops() {
        let loop_text = "1979年苏联入侵阿富汗";
        let mut items = vec![item(
            "subtitle",
            "1979年伊朗人质危机的伊朗革命",
            60000,
            62040,
        )];
        for n in 0..4 {
            items.push(item(
                "subtitle",
                loop_text,
                62040 + n * 1000,
                63040 + n * 1000,
            ));
        }
        let found = check(&items).unwrap();
        let loops: Vec<_> = found
            .iter()
            .filter(|f| f.problem.contains("ซ้ำ 4 ท่อน"))
            .collect();
        assert_eq!(loops.len(), 3, "{found:?}");
        assert_eq!(loops[0].start_ms, 63040);
        // two in a row is normal speech ("yes. yes.")
        let p = problems(&[
            item("subtitle", "ใช่", 0, 1000),
            item("subtitle", "ใช่", 1000, 2000),
        ]);
        assert!(p.iter().all(|s| !s.contains("ท่อนติดกัน")), "{p:?}");
    }
    #[test]
    fn rejects_bad_input() {
        assert!(check(&[item("nope", "x", 0, 1)]).is_err());
        assert!(check(&[item("subtitle", "x", 5, 1)]).is_err());
    }
}
