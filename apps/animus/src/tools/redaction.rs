#![allow(dead_code)]

use std::env;
use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use crate::tools::ToolOutput;

const REDACTED: &str = "[redacted]";
const MIN_SECRET_LEN: usize = 4;

pub fn redact_text(raw: &str) -> String {
    redact_text_with_values(raw, &collect_secret_values())
}

pub fn redact_tool_output(output: ToolOutput) -> ToolOutput {
    let secrets = collect_secret_values();
    redact_tool_output_with_values(output, &secrets)
}

pub fn redact_tool_output_with_values(mut output: ToolOutput, secrets: &[String]) -> ToolOutput {
    output.content = redact_text_with_values(&output.content, secrets);
    output.stdout = output
        .stdout
        .into_iter()
        .map(|line| redact_text_with_values(&line, secrets))
        .collect();
    output.stderr = output
        .stderr
        .into_iter()
        .map(|line| redact_text_with_values(&line, secrets))
        .collect();
    output
}

fn redact_text_with_values(raw: &str, secrets: &[String]) -> String {
    let mut redacted = raw.to_string();
    for secret in normalized_secrets(secrets) {
        redacted = redacted.replace(&secret, REDACTED);
    }
    redacted
}

fn collect_secret_values() -> Vec<String> {
    let mut values = env::vars()
        .filter(|(key, _)| is_secret_env_key(key))
        .map(|(_, value)| value)
        .filter(|value| is_redactable_secret(value))
        .collect::<Vec<_>>();

    if let Some(path) = animus_secrets_path() {
        if let Ok(raw) = fs::read_to_string(path) {
            if let Ok(json) = serde_json::from_str::<Value>(&raw) {
                collect_json_strings(&json, &mut values);
            }
        }
    }

    normalized_secrets(&values)
}

fn normalized_secrets(values: &[String]) -> Vec<String> {
    let mut values = values
        .iter()
        .map(|value| value.trim().to_string())
        .filter(|value| is_redactable_secret(value))
        .collect::<Vec<_>>();
    values.sort_by_key(|value| std::cmp::Reverse(value.len()));
    values.dedup();
    values
}

fn collect_json_strings(value: &Value, values: &mut Vec<String>) {
    match value {
        Value::String(raw) if is_redactable_secret(raw) => values.push(raw.clone()),
        Value::Array(items) => {
            for item in items {
                collect_json_strings(item, values);
            }
        }
        Value::Object(map) => {
            for value in map.values() {
                collect_json_strings(value, values);
            }
        }
        _ => {}
    }
}

fn is_secret_env_key(key: &str) -> bool {
    let key = key.to_ascii_uppercase();
    key.contains("TOKEN")
        || key.contains("SECRET")
        || key.contains("PASSWORD")
        || key.contains("API_KEY")
        || key.ends_with("_KEY")
}

fn is_redactable_secret(value: &str) -> bool {
    value.trim().len() >= MIN_SECRET_LEN
}

fn animus_secrets_path() -> Option<PathBuf> {
    env::var_os("ANIMUS_SECRETS_PATH")
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("USERPROFILE")
                .or_else(|| env::var_os("HOME"))
                .map(|home| PathBuf::from(home).join(".animus").join("secrets.json"))
        })
}
