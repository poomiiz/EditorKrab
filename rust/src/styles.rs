//! Validate optional style parameters before a draft becomes durable.
use serde::Deserialize;
use serde_json::Value;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Text {
    text: String,
    font: String,
    size: u16,
    color: String,
    x: f64,
    y: f64,
    // optional effects; absent means the defaults export always used (outline 4, shadow 1, no box, no entrance)
    #[serde(default)]
    outline: Option<u8>,
    #[serde(default)]
    outline_color: Option<String>,
    #[serde(default)]
    shadow: Option<u8>,
    #[serde(default)]
    #[allow(dead_code)] // typed so a non-boolean is rejected
    box_on: Option<bool>,
    #[serde(default)]
    box_color: Option<String>,
    #[serde(default)]
    enter: Option<String>,
    #[serde(default)]
    shadow_blur: Option<u8>,
    #[serde(default)]
    shadow_color: Option<String>,
    #[serde(default)]
    blend: Option<String>,
}
fn hex(v: &str) -> bool {
    v.len() == 7 && v.starts_with('#') && v[1..].bytes().all(|b| b.is_ascii_hexdigit())
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Styles {
    headline: Text,
    speech: Text,
    emphasis: Text,
    english: Text,
}
fn valid_font(value: &str) -> bool {
    if ["system", "tahoma", "thai"].contains(&value) {
        return true;
    }
    let Some(name) = value.strip_prefix("family:") else {
        return false;
    };
    !name.is_empty()
        && name.trim() == name
        && name.encode_utf16().count() <= 128
        && !name
            .chars()
            .any(|c| c.is_control() || ['"', '\'', '\\', ',', ';', '{', '}', '<', '>'].contains(&c))
}
fn valid_text(v: &Text) -> bool {
    v.text.encode_utf16().count() <= 80
        && !v.text.contains(['\r', '\n'])
        && valid_font(&v.font)
        && (24..=160).contains(&v.size)
        && v.color.len() == 7
        && v.color.starts_with('#')
        && v.color[1..].bytes().all(|b| b.is_ascii_hexdigit())
        && v.x.is_finite()
        && (5.0..=95.0).contains(&v.x)
        && v.y.is_finite()
        && (5.0..=95.0).contains(&v.y)
        && v.outline.is_none_or(|n| n <= 20)
        && v.shadow.is_none_or(|n| n <= 20)
        && v.outline_color.as_deref().is_none_or(hex)
        && v.box_color.as_deref().is_none_or(hex)
        && v.enter.as_deref().is_none_or(|e| ["none", "fade", "pop", "wipe", "words"].contains(&e))
        && v.shadow_blur.is_none_or(|n| n <= 20)
        && v.shadow_color.as_deref().is_none_or(hex)
        && v.blend.as_deref().is_none_or(|b| BLENDS.contains(&b))
}
/// How a text layer mixes with the video under it.
pub const BLENDS: [&str; 4] = ["normal", "add", "screen", "multiply"];
pub fn valid(document: &Value) -> bool {
    if !crate::transcript_edits::valid(document) {
        return false;
    }
    if let Some(items) = document.get("headline_items") {
        let Some(items) = items.as_array() else {
            return false;
        };
        if items.len() > 100 {
            return false;
        }
        let mut ids = std::collections::HashSet::new();
        for item in items {
            let Some(map) = item.as_object() else {
                return false;
            };
            let Some(id) = item["id"].as_str() else {
                return false;
            };
            if id.is_empty()
                || id.len() > 128
                || !id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                || !ids.insert(id)
            {
                return false;
            }
            let mut style = map.clone();
            style.remove("id");
            let Ok(style) = serde_json::from_value::<Text>(Value::Object(style)) else {
                return false;
            };
            if !valid_text(&style) {
                return false;
            }
        }
    }

    if let Some(roles) = document.get("text_roles") {
        let Some(roles) = roles.as_array() else {
            return false;
        };
        if roles.len() > 3
            || roles.iter().any(|r| {
                !["speech", "emphasis", "english"]
                    .iter()
                    .any(|s| r.as_str() == Some(s))
            })
            || roles
                .iter()
                .map(|v| v.as_str())
                .collect::<std::collections::HashSet<_>>()
                .len()
                != roles.len()
        {
            return false;
        }
    }
    if document.get("headline_template").is_some_and(|v| {
        !["plain", "plate", "emphasis"]
            .iter()
            .any(|s| v.as_str() == Some(s))
    }) {
        return false;
    }
    if document
        .get("headline_reply")
        .is_some_and(|v| v.as_str().is_none_or(|s| s.encode_utf16().count() > 32000))
    {
        return false;
    }
    if document.get("flow_version").is_some_and(|v| {
        v.as_u64() != Some(2)
            || document["step"]
                .as_u64()
                .is_none_or(|v| !(1..=6).contains(&v))
    }) {
        return false;
    }

    let Some(value) = document.get("text_styles") else {
        return true;
    };
    let Ok(styles) = serde_json::from_value::<Styles>(value.clone()) else {
        return false;
    };
    [
        &styles.headline,
        &styles.speech,
        &styles.emphasis,
        &styles.english,
    ]
    .into_iter()
    .all(|v| valid_text(v))
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::drafts::{Error, Store};
    use serde_json::json;
    use std::path::Path;
    #[test]
    fn custom_font_family_keeps_legacy_values_and_rejects_css() {
        for font in [
            "system",
            "tahoma",
            "thai",
            "family:Noto Sans Thai",
            "family:ฟอนต์ไทย",
            "family:TH Sarabun New",
        ] {
            assert!(valid_font(font));
        }
        for font in [
            "external",
            "family:",
            "family: leading",
            "family:a\nb",
            "family:a,b",
            "family:a; color:red",
            "family:a\"b",
        ] {
            assert!(!valid_font(font));
        }
        assert!(!valid_font(&format!("family:{}", "x".repeat(129))));
    }
    #[test]
    fn headline_items_are_independent_and_validated() {
        let item =
            json!({"id":"a","text":"HL","font":"system","size":80,"color":"#ececf1","x":50,"y":16});
        let mut draft = json!({"headline_items":[item]});
        assert!(valid(&draft));
        let duplicate = draft["headline_items"][0].clone();
        draft["headline_items"]
            .as_array_mut()
            .unwrap()
            .push(duplicate);
        assert!(!valid(&draft));
        draft["headline_items"][1]["id"] = json!("b");
        assert!(valid(&draft));
        draft["headline_items"][1]["size"] = json!(161);
        assert!(!valid(&draft));
        draft["headline_items"][1]["size"] = json!(80);
        draft["headline_items"][1]["unknown"] = json!(true);
        assert!(!valid(&draft));
    }
    #[test]
    fn component_choices_and_flow_versions_are_checked() {
        assert!(valid(
            &json!({"flow_version":2,"step":6,"text_roles":[],"headline_template":"plate"})
        ));
        assert!(valid(&json!({"step":5})));
        for invalid in [
            json!({"text_roles":["headline"]}),
            json!({"text_roles":["speech","speech"]}),
            json!({"headline_template":"unknown"}),
            json!({"flow_version":2,"step":7}),
            json!({"headline_reply":"x".repeat(32001)}),
        ] {
            assert!(!valid(&invalid));
        }
    }
    #[test]
    fn invalid_styles_cannot_replace_acknowledged_draft() {
        let text = json!({"text":"ทดสอบ English","font":"system","size":64,"color":"#ececf1","x":50,"y":70});
        let draft =
            json!({"text_styles":{"headline":text,"speech":text,"emphasis":text,"english":text}});
        assert!(valid(&draft));
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        store.initialize_jobs().unwrap();
        store.save("style-test", "first", 0, &draft).unwrap();
        for (field, value) in [
            ("size", json!(0)),
            ("x", json!(100)),
            ("text", json!("x".repeat(81))),
            ("color", json!("red")),
            ("font", json!("external")),
            ("text", json!("a\nb")),
        ] {
            let mut bad = draft.clone();
            bad["text_styles"]["speech"][field] = value;
            assert!(matches!(
                store.save("style-test", "bad", 1, &bad),
                Err(Error::InvalidInput)
            ));
            assert_eq!(store.read("style-test").unwrap().unwrap().revision, 1);
        }
        assert_eq!(store.read("style-test").unwrap().unwrap().document, draft);
    }
}
