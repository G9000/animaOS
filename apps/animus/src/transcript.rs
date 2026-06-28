#![allow(dead_code)]

use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub enum TranscriptItem {
    User {
        content: String,
    },
    Assistant {
        content: String,
        streaming: bool,
    },
    Reasoning {
        content: String,
    },
    ToolCall {
        tool_call_id: String,
        tool_name: String,
        args: Value,
    },
    ToolReturn {
        tool_call_id: String,
        tool_name: String,
        result: String,
        is_error: bool,
    },
    Approval {
        run_id: i64,
        tool_call_id: String,
        tool_name: String,
        args: Value,
    },
    Shell {
        command: String,
        status: String,
        output: String,
    },
    FileChange {
        path: String,
        summary: String,
    },
    Todo {
        summary: String,
    },
    Notice {
        message: String,
    },
    Search {
        query: String,
        results: usize,
    },
    Session {
        message: String,
    },
    Error {
        code: String,
        message: String,
    },
}

impl TranscriptItem {
    pub fn render_plain(&self) -> String {
        match self {
            TranscriptItem::User { content } => format!("you: {content}"),
            TranscriptItem::Assistant { content, streaming } => {
                if *streaming {
                    format!("anima: {content} ...")
                } else {
                    format!("anima: {content}")
                }
            }
            TranscriptItem::Reasoning { content } => format!("reasoning: {content}"),
            TranscriptItem::ToolCall {
                tool_call_id,
                tool_name,
                args,
            } => format!("tool {tool_name} {tool_call_id}: {args}"),
            TranscriptItem::ToolReturn {
                tool_call_id,
                tool_name,
                result,
                is_error,
            } => {
                let status = if *is_error { "error" } else { "ok" };
                format!("tool-result {tool_name} {tool_call_id} [{status}]: {result}")
            }
            TranscriptItem::Approval {
                run_id,
                tool_call_id,
                tool_name,
                args,
            } => format!("approval {run_id} {tool_call_id} {tool_name}: {args}"),
            TranscriptItem::Shell {
                command,
                status,
                output,
            } => {
                format!("shell {command} [{status}]: {output}")
            }
            TranscriptItem::FileChange { path, summary } => format!("file {path}: {summary}"),
            TranscriptItem::Todo { summary } => format!("todo: {summary}"),
            TranscriptItem::Notice { message } => format!("notice: {message}"),
            TranscriptItem::Search { query, results } => {
                format!("search {query}: {results} results")
            }
            TranscriptItem::Session { message } => format!("session: {message}"),
            TranscriptItem::Error { code, message } => format!("error {code}: {message}"),
        }
    }
}

pub fn append_assistant_token(items: &mut Vec<TranscriptItem>, token: &str) {
    match items.last_mut() {
        Some(TranscriptItem::Assistant { content, streaming }) if *streaming => {
            content.push_str(token);
        }
        _ => items.push(TranscriptItem::Assistant {
            content: token.to_string(),
            streaming: true,
        }),
    }
}

pub fn finish_streaming_assistant(items: &mut [TranscriptItem]) {
    if let Some(TranscriptItem::Assistant { streaming, .. }) = items.iter_mut().rev().find(|item| {
        matches!(
            item,
            TranscriptItem::Assistant {
                streaming: true,
                ..
            }
        )
    }) {
        *streaming = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transcript_items_render_human_readable_history_cells() {
        let items = vec![
            TranscriptItem::User {
                content: "hello".to_string(),
            },
            TranscriptItem::Assistant {
                content: "hi".to_string(),
                streaming: true,
            },
            TranscriptItem::Shell {
                command: "pwd".to_string(),
                status: "ok".to_string(),
                output: "/repo".to_string(),
            },
            TranscriptItem::FileChange {
                path: "src/main.rs".to_string(),
                summary: "edited".to_string(),
            },
            TranscriptItem::Todo {
                summary: "write tests".to_string(),
            },
            TranscriptItem::Search {
                query: "memory".to_string(),
                results: 3,
            },
            TranscriptItem::Approval {
                run_id: 1,
                tool_call_id: "call-1".to_string(),
                tool_name: "bash".to_string(),
                args: serde_json::json!({"command":"rm -rf tmp"}),
            },
            TranscriptItem::Error {
                code: "BAD_REQUEST".to_string(),
                message: "no".to_string(),
            },
        ];

        let rendered: Vec<String> = items.iter().map(TranscriptItem::render_plain).collect();

        assert_eq!(rendered[0], "you: hello");
        assert_eq!(rendered[1], "anima: hi ...");
        assert!(rendered[2].contains("shell pwd [ok]"));
        assert!(rendered[3].contains("file src/main.rs: edited"));
        assert_eq!(rendered[4], "todo: write tests");
        assert_eq!(rendered[5], "search memory: 3 results");
        assert!(rendered[6].contains("approval 1 call-1 bash"));
        assert_eq!(rendered[7], "error BAD_REQUEST: no");
    }

    #[test]
    fn assistant_tokens_append_to_existing_streaming_cell() {
        let mut items = Vec::new();

        append_assistant_token(&mut items, "hel");
        append_assistant_token(&mut items, "lo");

        assert_eq!(
            items,
            vec![TranscriptItem::Assistant {
                content: "hello".to_string(),
                streaming: true,
            }]
        );
    }

    #[test]
    fn finish_streaming_assistant_finishes_latest_assistant_before_tool_rows() {
        let mut items = vec![
            TranscriptItem::Assistant {
                content: "checking".to_string(),
                streaming: true,
            },
            TranscriptItem::ToolCall {
                tool_call_id: "call-1".to_string(),
                tool_name: "bash".to_string(),
                args: serde_json::json!({"command":"cargo test"}),
            },
            TranscriptItem::ToolReturn {
                tool_call_id: "call-1".to_string(),
                tool_name: "bash".to_string(),
                result: "ok".to_string(),
                is_error: false,
            },
        ];

        finish_streaming_assistant(&mut items);

        assert!(matches!(
            &items[0],
            TranscriptItem::Assistant {
                streaming: false,
                ..
            }
        ));
    }
}
