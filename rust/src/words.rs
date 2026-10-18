//! Thai word boundaries without Python: newmm (nlpo3) over the PyThaiNLP CC0 word list.
use nlpo3::tokenizer::{newmm::NewmmTokenizer, tokenizer_trait::Tokenizer};
use std::sync::OnceLock;

const DICTIONARY: &str = include_str!("../data/words_th.txt");

fn tokenizer() -> &'static NewmmTokenizer {
    static TOKENIZER: OnceLock<NewmmTokenizer> = OnceLock::new();
    TOKENIZER.get_or_init(|| {
        NewmmTokenizer::from_word_list(DICTIONARY.lines().map(str::to_owned).collect())
    })
}

/// True when `word` is a dictionary word.
pub fn known(word: &str) -> bool {
    static WORDS: OnceLock<std::collections::HashSet<&'static str>> = OnceLock::new();
    WORDS
        .get_or_init(|| DICTIONARY.lines().collect())
        .contains(word)
}

/// Words of `text`, whitespace kept as its own pieces, so joining them gives the text back.
pub fn split(text: &str) -> Result<Vec<String>, String> {
    let words = tokenizer()
        .segment(text, true, false)
        .map_err(|_| "word_split_failed")?;
    if words.concat() != text {
        return Err("word_split_lost_text".into());
    }
    Ok(words)
}

#[cfg(test)]
mod tests {
    #[test]
    fn thai_words_join_back() {
        let text = "สวัสดีครับ วันนี้โอเคคัป ไปที่กองทัพ";
        let words = super::split(text).unwrap();
        assert_eq!(words.concat(), text);
        for word in ["สวัสดี", "ครับ", " ", "โอเค", "กองทัพ"]
        {
            assert!(
                words.iter().any(|w| w == word),
                "{word} missing in {words:?}"
            );
        }
    }
}
