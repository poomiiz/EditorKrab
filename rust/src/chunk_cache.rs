//! Verified engine JSON is retained separately from job/SSE results.
use crate::drafts::{Error, Store};
use rusqlite::{OptionalExtension, Transaction, params};
use sha2::{Digest, Sha256};

#[derive(Clone, Debug)]
pub struct Candidate {
    pub key: String,
    pub raw: Vec<u8>,
    pub sha256: String,
}
impl Candidate {
    pub fn new(key: String, raw: Vec<u8>) -> Self {
        let sha256 = format!("{:x}", Sha256::digest(&raw));
        Self { key, raw, sha256 }
    }
    pub fn valid(&self) -> bool {
        self.key.len() == 64
            && self.key.bytes().all(|b| b.is_ascii_hexdigit())
            && self.raw.len() <= crate::transcription::MAX_JSON as usize
            && self.sha256 == format!("{:x}", Sha256::digest(&self.raw))
    }
}
pub(crate) fn save(tx: &Transaction<'_>, candidate: &Candidate) -> Result<(), Error> {
    if !candidate.valid() {
        return Err(Error::TranscriptionCacheCorrupt);
    }
    let old: Option<(Vec<u8>, String)> = tx
        .query_row(
            "SELECT raw,sha256 FROM transcription_cache WHERE key=?1",
            [&candidate.key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    if let Some((raw, sha256)) = old {
        if !(Candidate {
            key: candidate.key.clone(),
            raw,
            sha256,
        })
        .valid()
        {
            return Err(Error::TranscriptionCacheCorrupt);
        }
    } else {
        tx.execute(
            "INSERT INTO transcription_cache(key,raw,sha256) VALUES(?1,?2,?3)",
            params![candidate.key, candidate.raw, candidate.sha256],
        )?;
    }
    Ok(())
}
impl Store {
    pub fn transcription_cache(
        &self,
        job_id: &str,
        key: &str,
        source_sha256: Option<&str>,
    ) -> Result<Option<Vec<u8>>, Error> {
        let job = self.job(job_id)?;
        if job.status != "running" || job.cancel_requested {
            return Err(Error::JobState);
        }
        if let Some(actual) = source_sha256 {
            let expected=self.0.prepare("SELECT json_extract(request,'$.source_sha256') FROM transcription_requests,json_each(job_ids) WHERE json_each.value=?1")?.query_map([job_id],|r|r.get::<_,Option<String>>(0))?.collect::<Result<Vec<_>,_>>()?;
            if expected
                .into_iter()
                .flatten()
                .any(|expected| expected != actual)
            {
                return Err(Error::SourceChanged);
            }
        }
        let raw: Option<(Vec<u8>, String)> = self
            .0
            .query_row(
                "SELECT raw,sha256 FROM transcription_cache WHERE key=?1",
                [key],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        raw.map(|(raw, sha256)| {
            let candidate = Candidate {
                key: key.into(),
                raw,
                sha256,
            };
            if !candidate.valid() {
                Err(Error::TranscriptionCacheCorrupt)
            } else {
                Ok(candidate.raw)
            }
        })
        .transpose()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn cache_checksum_and_running_writer_guard_fail_closed() {
        let mut s = Store::open(std::path::Path::new(":memory:")).unwrap();
        s.save(
            "project",
            "initial",
            0,
            &json!({"files":[{"source_id":"one","path":"one.wav","size":10,"modified_ns":"1"}]}),
        )
        .unwrap();
        let job = s.claim_job().unwrap().unwrap();
        let candidate = Candidate::new("a".repeat(64), b"{}".to_vec());
        {
            let tx = s.0.transaction().unwrap();
            save(&tx, &candidate).unwrap();
            tx.commit().unwrap();
        }
        assert_eq!(
            s.transcription_cache(&job.id, &candidate.key, None)
                .unwrap(),
            Some(b"{}".to_vec())
        );
        s.0.execute("INSERT INTO transcription_requests(project_id,request_id,request,job_ids) VALUES('project','request',?1,?2)",params![json!({"source_sha256":"a".repeat(64)}).to_string(),json!([job.id]).to_string()]).unwrap();
        assert!(matches!(
            s.transcription_cache(&job.id, &candidate.key, Some(&"b".repeat(64))),
            Err(Error::SourceChanged)
        ));
        s.0.execute("UPDATE transcription_cache SET raw=X'7b'", [])
            .unwrap();
        assert!(matches!(
            s.transcription_cache(&job.id, &candidate.key, None),
            Err(Error::TranscriptionCacheCorrupt)
        ));
        {
            let tx = s.0.transaction().unwrap();
            assert!(matches!(
                save(&tx, &candidate),
                Err(Error::TranscriptionCacheCorrupt)
            ));
        }
        s.cancel_job(&job.id).unwrap();
        assert!(matches!(
            s.transcription_cache(&job.id, &candidate.key, None),
            Err(Error::JobState)
        ));
        s.finish_job_cached(
            &job.id,
            Ok(json!({})),
            Some(&Candidate::new("b".repeat(64), b"invalid".to_vec())),
        )
        .unwrap();
        assert_eq!(s.job(&job.id).unwrap().status, "cancelled");
        assert_eq!(
            s.0.query_row("SELECT count(*) FROM transcription_cache", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            1
        );
    }
}
