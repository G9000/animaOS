#![allow(dead_code)]

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SlashCommand {
    Help,
    Clear,
    Cancel,
    Reconnect,
    Permissions,
    Status,
    Diff,
    Spawns,
    CancelSpawn,
    Quit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgumentMode {
    None,
    Optional,
    Required,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommandMetadata {
    pub command: SlashCommand,
    pub name: &'static str,
    pub description: &'static str,
    pub argument_mode: ArgumentMode,
    pub available_while_busy: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandInvocation {
    pub command: SlashCommand,
    pub args: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommandParseError {
    NotCommand,
    UnknownCommand(String),
    MissingArgument(&'static str),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommandEffect {
    None,
    ShowHelp,
    ClearTranscript,
    CancelRun { run_id: i64 },
    Reconnect,
    SetPermissions(String),
    ShowStatus,
    ShowDiff,
    ShowSpawns,
    CancelSpawn { id: String },
    Quit,
}

pub fn command_registry() -> Vec<CommandMetadata> {
    vec![
        meta(
            SlashCommand::Help,
            "help",
            "Show available commands",
            ArgumentMode::None,
            true,
        ),
        meta(
            SlashCommand::Clear,
            "clear",
            "Clear the transcript",
            ArgumentMode::None,
            false,
        ),
        meta(
            SlashCommand::Cancel,
            "cancel",
            "Cancel the active run",
            ArgumentMode::None,
            true,
        ),
        meta(
            SlashCommand::Reconnect,
            "reconnect",
            "Reconnect to ANIMA",
            ArgumentMode::None,
            false,
        ),
        meta(
            SlashCommand::Permissions,
            "permissions",
            "Set permission mode",
            ArgumentMode::Optional,
            false,
        ),
        meta(
            SlashCommand::Status,
            "status",
            "Show session status",
            ArgumentMode::None,
            true,
        ),
        meta(
            SlashCommand::Diff,
            "diff",
            "Show workspace diff",
            ArgumentMode::None,
            false,
        ),
        meta(
            SlashCommand::Spawns,
            "spawns",
            "List background spawns",
            ArgumentMode::None,
            true,
        ),
        meta(
            SlashCommand::CancelSpawn,
            "cancel-spawn",
            "Cancel a background spawn",
            ArgumentMode::Required,
            false,
        ),
        meta(
            SlashCommand::Quit,
            "quit",
            "Exit Animus",
            ArgumentMode::None,
            true,
        ),
    ]
}

pub fn parse_command(raw: &str) -> Result<CommandInvocation, CommandParseError> {
    let Some(command_line) = raw.trim().strip_prefix('/') else {
        return Err(CommandParseError::NotCommand);
    };
    let (name, args) = match command_line.split_once(char::is_whitespace) {
        Some((name, rest)) => (name, rest.trim()),
        None => (command_line, ""),
    };
    let Some(metadata) = command_registry()
        .into_iter()
        .find(|meta| meta.name == name)
    else {
        return Err(CommandParseError::UnknownCommand(name.to_string()));
    };
    if matches!(metadata.argument_mode, ArgumentMode::Required) && args.is_empty() {
        return Err(CommandParseError::MissingArgument(metadata.name));
    }
    Ok(CommandInvocation {
        command: metadata.command,
        args: args.to_string(),
    })
}

pub fn autocomplete(prefix: &str, busy: bool) -> Vec<CommandMetadata> {
    let normalized = prefix.trim_start_matches('/');
    command_registry()
        .into_iter()
        .filter(|meta| meta.name.starts_with(normalized))
        .filter(|meta| !busy || meta.available_while_busy)
        .collect()
}

fn meta(
    command: SlashCommand,
    name: &'static str,
    description: &'static str,
    argument_mode: ArgumentMode,
    available_while_busy: bool,
) -> CommandMetadata {
    CommandMetadata {
        command,
        name,
        description,
        argument_mode,
        available_while_busy,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parser_recognizes_supported_commands_and_args() {
        let cases = [
            ("/help", SlashCommand::Help, ""),
            ("/clear", SlashCommand::Clear, ""),
            ("/cancel", SlashCommand::Cancel, ""),
            ("/reconnect", SlashCommand::Reconnect, ""),
            (
                "/permissions workspace-write",
                SlashCommand::Permissions,
                "workspace-write",
            ),
            ("/status", SlashCommand::Status, ""),
            ("/diff", SlashCommand::Diff, ""),
            ("/spawns", SlashCommand::Spawns, ""),
            (
                "/cancel-spawn spawn-1",
                SlashCommand::CancelSpawn,
                "spawn-1",
            ),
            ("/quit", SlashCommand::Quit, ""),
        ];

        for (raw, command, args) in cases {
            let invocation = parse_command(raw).unwrap();
            assert_eq!(invocation.command, command);
            assert_eq!(invocation.args, args);
        }
        assert!(parse_command("hello").is_err());
        assert!(parse_command("/missing").is_err());
    }

    #[test]
    fn metadata_sorting_availability_and_autocomplete_are_stable() {
        let names: Vec<&'static str> = command_registry()
            .into_iter()
            .map(|meta| meta.name)
            .collect();
        assert_eq!(
            names,
            vec![
                "help",
                "clear",
                "cancel",
                "reconnect",
                "permissions",
                "status",
                "diff",
                "spawns",
                "cancel-spawn",
                "quit",
            ]
        );

        let busy: Vec<&'static str> = autocomplete("/c", true)
            .into_iter()
            .map(|meta| meta.name)
            .collect();
        assert_eq!(busy, vec!["cancel"]);

        let idle: Vec<&'static str> = autocomplete("/c", false)
            .into_iter()
            .map(|meta| meta.name)
            .collect();
        assert_eq!(idle, vec!["clear", "cancel", "cancel-spawn"]);
    }
}
