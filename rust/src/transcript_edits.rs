//! Corrections retain their source text and cannot be attached to another job or clock.
use crate::{drafts::Error, jobs::Task};
use rusqlite::{OptionalExtension, Transaction, params};
use serde::Deserialize;
use serde_json::Value;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Edit {
    pub job_id: String,
    pub segment_id: String,
    pub source_sha256: String,
    pub original_text: String,
    pub text: String,
}
pub(crate) fn edits(document: &Value) -> Result<Vec<Edit>, Error> {
    let Some(value) = document.get("transcript_edits") else {
        return Ok(Vec::new());
    };
    let edits: Vec<Edit> =
        serde_json::from_value(value.clone()).map_err(|_| Error::InvalidInput)?;
    if edits.len() > 10000 {
        return Err(Error::InvalidInput);
    }
    let mut ids = std::collections::HashSet::new();
    for edit in &edits {
        if [&edit.job_id, &edit.segment_id].iter().any(|v| {
            v.is_empty() || v.encode_utf16().count() > 256 || v.chars().any(char::is_control)
        }) || edit.source_sha256.len() != 64
            || !edit.source_sha256.bytes().all(|b| b.is_ascii_hexdigit())
            || [&edit.original_text, &edit.text]
                .iter()
                .any(|v| v.encode_utf16().count() > 2000)
            || !ids.insert((&edit.job_id, &edit.segment_id))
        {
            return Err(Error::InvalidInput);
        }
    }
    Ok(edits)
}
pub fn valid(document: &Value) -> bool {
    edits(document).is_ok()
}
pub(crate) fn validate_saved(
    tx: &Transaction<'_>,
    project: &str,
    document: &Value,
) -> Result<(), Error> {
    for edit in edits(document)? {
        referenced_job(tx, &edit, Some(project))?;
    }
    Ok(())
}
pub(crate) fn referenced_job(
    tx: &Transaction<'_>,
    edit: &Edit,
    project: Option<&str>,
) -> Result<crate::jobs::Job, Error> {
    let job=tx.query_row(&format!("SELECT {} FROM jobs WHERE id=?1 AND status='succeeded' AND (?2 IS NULL OR project_id=?2)",crate::jobs::COLUMNS),params![edit.job_id,project],crate::jobs::read_job).optional()?.ok_or(Error::InvalidInput)?;
    if !matches!(job.task, Task::Transcribe { .. }) {
        return Err(Error::InvalidInput);
    }
    let result = job.result.as_ref().ok_or(Error::InvalidInput)?;
    if result["source"]["sha256"].as_str() != Some(&edit.source_sha256)
        || !result["transcript"]["segments"]
            .as_array()
            .is_some_and(|segments| {
                segments.iter().any(|segment| {
                    segment["id"].as_str() == Some(&edit.segment_id)
                        && segment["text"].as_str() == Some(&edit.original_text)
                })
            })
    {
        return Err(Error::InvalidInput);
    }
    Ok(job)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn correction_bounds_and_identity_are_validated() {
        let edit = json!({"job_id":"project.run.chunk-0","segment_id":"source:run:0:s0","source_sha256":"a".repeat(64),"original_text":"พูดเดิม","text":"แก้คำ"});
        let mut d = json!({"transcript_edits":[edit]});
        assert!(valid(&d));
        let copy = d["transcript_edits"][0].clone();
        d["transcript_edits"].as_array_mut().unwrap().push(copy);
        assert!(!valid(&d));
        d["transcript_edits"].as_array_mut().unwrap().pop();
        d["transcript_edits"][0]["text"] = json!("😀".repeat(1001));
        assert!(!valid(&d));
        let mut s = crate::drafts::Store::open(std::path::Path::new(":memory:")).unwrap();
        assert!(matches!(s.save("p","bad",0,&json!({"transcript_edits":[{"job_id":"x","segment_id":"s","source_sha256":"a".repeat(64),"original_text":"one","text":"two"}]})),Err(Error::InvalidInput)));
        assert!(s.read("p").unwrap().is_none());
    }
}
