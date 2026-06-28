#![allow(dead_code)]

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct InputBuffer {
    text: String,
    cursor: usize,
    history: Vec<String>,
    history_index: Option<usize>,
    draft_before_history: String,
}

impl InputBuffer {
    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn cursor(&self) -> usize {
        self.cursor
    }

    pub fn insert_char(&mut self, ch: char) {
        self.text.insert(self.cursor, ch);
        self.cursor += ch.len_utf8();
        self.history_index = None;
    }

    pub fn insert_str(&mut self, text: &str) {
        for ch in text.chars() {
            self.insert_char(ch);
        }
    }

    pub fn insert_newline(&mut self) {
        self.insert_char('\n');
    }

    pub fn backspace(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let previous = previous_char_boundary(&self.text, self.cursor);
        self.text.replace_range(previous..self.cursor, "");
        self.cursor = previous;
    }

    pub fn delete(&mut self) {
        if self.cursor >= self.text.len() {
            return;
        }
        let next = next_char_boundary(&self.text, self.cursor);
        self.text.replace_range(self.cursor..next, "");
    }

    pub fn move_left(&mut self) {
        if self.cursor > 0 {
            self.cursor = previous_char_boundary(&self.text, self.cursor);
        }
    }

    pub fn move_right(&mut self) {
        if self.cursor < self.text.len() {
            self.cursor = next_char_boundary(&self.text, self.cursor);
        }
    }

    pub fn move_home(&mut self) {
        self.cursor = 0;
    }

    pub fn move_end(&mut self) {
        self.cursor = self.text.len();
    }

    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
        self.history_index = None;
    }

    pub fn submit(&mut self) -> Option<String> {
        let submitted = self.text.trim_end().to_string();
        if submitted.is_empty() {
            return None;
        }
        self.push_history(submitted.clone());
        self.clear();
        Some(submitted)
    }

    pub fn push_history(&mut self, entry: String) {
        if !entry.trim().is_empty() {
            self.history.push(entry);
        }
        self.history_index = None;
    }

    pub fn history_previous(&mut self) -> Option<&str> {
        if self.history.is_empty() {
            return None;
        }
        let next_index = match self.history_index {
            Some(index) if index > 0 => index - 1,
            Some(index) => index,
            None => {
                self.draft_before_history = self.text.clone();
                self.history.len() - 1
            }
        };
        self.history_index = Some(next_index);
        self.text = self.history[next_index].clone();
        self.cursor = self.text.len();
        Some(&self.text)
    }

    pub fn history_next(&mut self) -> Option<&str> {
        let index = self.history_index?;
        if index + 1 < self.history.len() {
            let next = index + 1;
            self.history_index = Some(next);
            self.text = self.history[next].clone();
        } else {
            self.history_index = None;
            self.text = self.draft_before_history.clone();
        }
        self.cursor = self.text.len();
        Some(&self.text)
    }
}

fn previous_char_boundary(text: &str, from: usize) -> usize {
    text[..from]
        .char_indices()
        .last()
        .map(|(index, _)| index)
        .unwrap_or(0)
}

fn next_char_boundary(text: &str, from: usize) -> usize {
    text[from..]
        .char_indices()
        .nth(1)
        .map(|(offset, _)| from + offset)
        .unwrap_or(text.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buffer_supports_insertion_deletion_cursor_and_multiline() {
        let mut input = InputBuffer::default();

        input.insert_str("helo");
        input.move_left();
        input.insert_char('l');
        input.move_end();
        input.insert_newline();
        input.insert_str("anima");
        input.move_left();
        input.delete();
        input.backspace();

        assert_eq!(input.text(), "hello\nani");
        assert_eq!(input.cursor(), input.text().len());
    }

    #[test]
    fn history_previous_and_next_restore_entries_and_draft() {
        let mut input = InputBuffer::default();
        input.push_history("first".to_string());
        input.push_history("second".to_string());
        input.insert_str("draft");

        assert_eq!(input.history_previous(), Some("second"));
        assert_eq!(input.text(), "second");
        assert_eq!(input.history_previous(), Some("first"));
        assert_eq!(input.text(), "first");
        assert_eq!(input.history_next(), Some("second"));
        assert_eq!(input.history_next(), Some("draft"));
        assert_eq!(input.text(), "draft");
    }
}
