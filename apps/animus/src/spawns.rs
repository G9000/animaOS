#![allow(dead_code)]

use std::collections::BTreeMap;

use crate::protocol::SpawnFrame;

#[derive(Debug, Clone, Default)]
pub struct SpawnState {
    spawns: BTreeMap<String, SpawnFrame>,
}

impl SpawnState {
    pub fn apply_frame(&mut self, frame: SpawnFrame) {
        self.spawns.insert(frame.id.clone(), frame);
    }

    pub fn active_count(&self) -> usize {
        self.spawns
            .values()
            .filter(|spawn| spawn.status.is_active())
            .count()
    }

    pub fn render_list(&self) -> String {
        if self.spawns.is_empty() {
            return "No background processes.".to_string();
        }
        self.spawns
            .values()
            .map(render_spawn)
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn cancel_spawn(&self, _id: &str) -> Result<(), String> {
        Err("cancel-spawn is not supported by the server yet".to_string())
    }
}

fn render_spawn(spawn: &SpawnFrame) -> String {
    let task = spawn.task.as_deref().unwrap_or("(no task)");
    let mut parts = vec![format!(
        "background process {} [{}] {}",
        spawn.id,
        spawn.status.as_str(),
        task
    )];
    if let Some(started_at) = &spawn.started_at {
        parts.push(format!("started {started_at}"));
    }
    if let Some(completed_at) = &spawn.completed_at {
        parts.push(format!("completed {completed_at}"));
    }
    parts.join(" | ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{SpawnFrame, SpawnStatus};

    #[test]
    fn spawn_state_tracks_statuses_and_running_count() {
        let mut state = SpawnState::default();

        state.apply_frame(SpawnFrame {
            id: "spawn-1".to_string(),
            status: SpawnStatus::Queued,
            task: Some("scan files".to_string()),
            started_at: None,
            completed_at: None,
            extra: Default::default(),
        });
        state.apply_frame(SpawnFrame {
            id: "spawn-1".to_string(),
            status: SpawnStatus::Running,
            task: Some("scan files".to_string()),
            started_at: Some("2026-06-27T00:00:00Z".to_string()),
            completed_at: None,
            extra: Default::default(),
        });
        state.apply_frame(SpawnFrame {
            id: "spawn-2".to_string(),
            status: SpawnStatus::Completed,
            task: Some("summarize docs".to_string()),
            started_at: None,
            completed_at: Some("2026-06-27T00:01:00Z".to_string()),
            extra: Default::default(),
        });

        assert_eq!(state.active_count(), 1);
        let rendered = state.render_list();
        assert!(rendered.contains("background process spawn-1 [running] scan files"));
        assert!(rendered.contains("background process spawn-2 [completed] summarize docs"));
        assert!(!rendered.contains("persona"));
    }

    #[test]
    fn cancel_spawn_reports_unsupported_without_server_support() {
        let state = SpawnState::default();

        assert_eq!(
            state.cancel_spawn("spawn-1"),
            Err("cancel-spawn is not supported by the server yet".to_string())
        );
    }
}
