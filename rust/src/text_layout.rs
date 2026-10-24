//! Measure shaped, installed fonts before splitting a subtitle or fitting an unbreakable word.
use serde_json::Value;
use std::sync::OnceLock;

fn database() -> &'static fontdb::Database {
    static DB: OnceLock<fontdb::Database> = OnceLock::new();
    DB.get_or_init(|| {
        let mut db = fontdb::Database::new();
        db.load_system_fonts();
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            db.load_fonts_dir(std::path::Path::new(&local).join("Microsoft/Windows/Fonts"));
        }
        db
    })
}

/// Preview the installed face used by the renderer, including in a browser on another OS.
/// The caller supplies a family name, never a filesystem path.
pub fn preview_font(family: &str, bold: bool, italic: bool) -> Option<Vec<u8>> {
    if family.is_empty() || family.len() > 128 || family.chars().any(char::is_control) { return None; }
    let db = database();
    let id = db.query(&fontdb::Query {
        families: &[fontdb::Family::Name(family)],
        weight: if bold { fontdb::Weight::BOLD } else { fontdb::Weight::NORMAL },
        style: if italic { fontdb::Style::Italic } else { fontdb::Style::Normal },
        ..Default::default()
    })?;
    db.with_face_data(id, |data, index| if index == 0 { Some(data.to_vec()) } else { None }).flatten()
}

pub fn width(text: &str, style: &Value) -> f64 {
    let db = database();
    let family = crate::export::font(style["font"].as_str().unwrap_or("system"));
    let families = [fontdb::Family::Name(family), fontdb::Family::Name("Leelawadee UI"), fontdb::Family::Name("Tahoma"), fontdb::Family::SansSerif];
    let face = db.query(&fontdb::Query {
        families: &families,
        weight: if style["bold"].as_bool() == Some(false) { fontdb::Weight::NORMAL } else { fontdb::Weight::BOLD },
        style: if style["italic"].as_bool() == Some(true) { fontdb::Style::Italic } else { fontdb::Style::Normal },
        ..Default::default()
    });
    let size = style["size"].as_f64().unwrap_or(64.0);
    let shown = match style["case"].as_str() {
        Some("upper") => text.to_uppercase(),
        Some("lower") => text.to_lowercase(),
        _ => text.to_owned(),
    };
    let measured = face.and_then(|id| db.with_face_data(id, |data, index| {
        let face = rustybuzz::Face::from_slice(data, index)?;
        let mut buffer = rustybuzz::UnicodeBuffer::new();
        buffer.push_str(&shown);
        let glyphs = rustybuzz::shape(&face, &[], buffer);
        // A missing glyph will be substituted by libass; use the conservative bound below.
        if glyphs.glyph_infos().iter().any(|g| g.glyph_id == 0) { return None; }
        Some(glyphs.glyph_positions().iter().map(|p| p.x_advance as f64).sum::<f64>() / face.units_per_em() as f64 * size)
    })).flatten();
    let count = shown.chars().filter(|c| !matches!(c, '\u{0e31}' | '\u{0e34}'..='\u{0e3a}' | '\u{0e47}'..='\u{0e4e}')).count();
    measured.unwrap_or(count as f64 * size)
        + style["spacing"].as_f64().unwrap_or(0.0) * count.saturating_sub(1) as f64
}

/// Width in the preview's 1080-wide coordinates, accounting for anchor, stroke and a safe edge.
pub fn available(style: &Value) -> f64 {
    let x = style["x"].as_f64().unwrap_or(50.0).clamp(0.0, 100.0) * 10.8;
    let edge = 54.0 + style["outline"].as_f64().unwrap_or(4.0)
        + if style["box_on"].as_bool() == Some(true) { style["box_pad_x"].as_f64().unwrap_or(12.0) } else { 0.0 };
    match style["align"].as_str() {
        Some("left") => 1080.0 - edge - x,
        Some("right") => x - edge,
        _ => 2.0 * (x - edge).min(1080.0 - edge - x),
    }.max(24.0)
}

pub fn fits(text: &str, style: &Value) -> bool { width(text, style) <= available(style) * 0.96 }

pub fn fitted(style: &Value, text: &str) -> Value { fitted_in(style, text, 1920.0) }
pub fn fitted_in(style: &Value, text: &str, height: f64) -> Value {
    let mut result = style.clone();
    let size = style["size"].as_f64().unwrap_or(64.0);
    let measured = width(text, style);
    if measured > 0.0 {
        let y = style["y"].as_f64().unwrap_or(50.0).clamp(0.0, 100.0) / 100.0;
        let pad = style["outline"].as_f64().unwrap_or(4.0) + if style["box_on"].as_bool() == Some(true) { style["box_pad_y"].as_f64().unwrap_or(12.0) } else { 0.0 };
        let vertical = (height * y.min(1.0-y) - 16.0 - pad).max(1.0) * 2.0 / 1.5;
        let fitted = (size * (available(style) * 0.96 / measured).min(1.0)).min(vertical).floor().max(1.0);
        result["size"] = serde_json::json!(fitted);
        if let Some(spacing) = style["spacing"].as_f64() {
            result["spacing"] = serde_json::json!(spacing * fitted / size);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preview_font_resolves_only_installed_family_names() {
        assert!(preview_font("", false, false).is_none());
        assert!(preview_font("../../private-file", false, false).is_none());
        assert!(preview_font("EditorKrab Unknown Font 98765", true, false).is_none());
        #[cfg(target_os = "windows")]
        assert!(preview_font("Segoe UI", false, false).is_some());
    }
    #[test]
    fn shaped_text_fits_with_offcentre_anchors_and_large_fonts() {
        for text in ["กินไปคุยไปทำไมคนชอบดูมากกว่าสอน", "Sometimes I make something and post it right away", "WWWWWWWWWWWWWWWWWWWW"] {
            let style = serde_json::json!({"font":"tahoma","size":125,"x":32,"outline":4,"spacing":100});
            let fit = fitted(&style, text);
            assert!(width(text, &fit) <= available(&fit), "{text}");
            assert!(fit["size"].as_f64().unwrap() <= 125.0);
        }
    }
}
