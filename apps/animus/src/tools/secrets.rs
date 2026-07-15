use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

pub(crate) fn substitute_saved_secrets(command: &str) -> String {
    let Some(path) = animus_secrets_path() else {
        return command.to_string();
    };
    let secrets = load_saved_secret_map(&path);
    if secrets.is_empty() {
        return command.to_string();
    }
    substitute_secret_placeholders(command, &secrets)
}

fn load_saved_secret_map(path: &Path) -> HashMap<String, String> {
    let Ok(raw) = fs::read_to_string(path) else {
        return HashMap::new();
    };
    let Ok(json) = serde_json::from_str::<Value>(&raw) else {
        return HashMap::new();
    };
    let mut secrets = HashMap::new();
    collect_secret_bindings(&json, &mut secrets);
    secrets
}

fn collect_secret_bindings(value: &Value, secrets: &mut HashMap<String, String>) {
    match value {
        Value::Object(map) => {
            for (key, value) in map {
                if let Value::String(secret) = value {
                    if is_shell_identifier(key) && !secret.is_empty() {
                        secrets.insert(key.clone(), secret.clone());
                    }
                } else {
                    collect_secret_bindings(value, secrets);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_secret_bindings(item, secrets);
            }
        }
        _ => {}
    }
}

fn substitute_secret_placeholders(command: &str, secrets: &HashMap<String, String>) -> String {
    let bytes = command.as_bytes();
    let mut output = String::with_capacity(command.len());
    let mut index = 0;

    while index < bytes.len() {
        if bytes[index] == b'$' {
            if index + 1 < bytes.len() && bytes[index + 1] == b'{' {
                if let Some(close_offset) = bytes[index + 2..].iter().position(|byte| *byte == b'}')
                {
                    let close_index = index + 2 + close_offset;
                    let name = &command[index + 2..close_index];
                    if is_shell_identifier(name) {
                        if let Some(secret) = secrets.get(name) {
                            output.push_str(secret);
                            index = close_index + 1;
                            continue;
                        }
                    }
                    output.push_str(&command[index..close_index + 1]);
                    index = close_index + 1;
                    continue;
                }
            } else if index + 1 < bytes.len() && is_identifier_start(bytes[index + 1]) {
                let start = index + 1;
                let mut end = start + 1;
                while end < bytes.len() && is_identifier_part(bytes[end]) {
                    end += 1;
                }
                let name = &command[start..end];
                if let Some(secret) = secrets.get(name) {
                    output.push_str(secret);
                } else {
                    output.push_str(&command[index..end]);
                }
                index = end;
                continue;
            }
        }

        let next = command[index..]
            .chars()
            .next()
            .expect("index should stay on char boundary");
        output.push(next);
        index += next.len_utf8();
    }

    output
}

fn is_shell_identifier(name: &str) -> bool {
    let bytes = name.as_bytes();
    if bytes.is_empty() || !is_identifier_start(bytes[0]) {
        return false;
    }
    bytes[1..].iter().all(|byte| is_identifier_part(*byte))
}

fn is_identifier_start(byte: u8) -> bool {
    byte == b'_' || byte.is_ascii_alphabetic()
}

fn is_identifier_part(byte: u8) -> bool {
    is_identifier_start(byte) || byte.is_ascii_digit()
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

#[cfg(test)]
pub(crate) fn install_test_saved_secrets() {
    use std::sync::OnceLock;

    static SECRETS_PATH: OnceLock<PathBuf> = OnceLock::new();
    let path = SECRETS_PATH.get_or_init(|| {
        let path = env::temp_dir().join(format!(
            "animus-saved-secret-substitution-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            r#"{"ANIMUS_TEST_TOKEN":"saved-secret-value","ANIMUS_DANGEROUS":"git push origin main"}"#,
        )
        .expect("test secrets fixture should be writable");
        path
    });
    env::set_var("ANIMUS_SECRETS_PATH", path);
}
