#![allow(dead_code)]

use std::collections::HashSet;

use serde_json::Value;

use crate::protocol::ClientFrame;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalKind {
    Shell { command: String },
    FileChange { path: String },
    Generic,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PendingApproval {
    pub run_id: i64,
    pub tool_call_id: String,
    pub tool_name: String,
    pub args: Value,
    pub kind: ApprovalKind,
}

impl PendingApproval {
    pub fn new(run_id: i64, tool_call_id: String, tool_name: String, args: Value) -> Self {
        let kind = classify_approval(&tool_name, &args);
        Self {
            run_id,
            tool_call_id,
            tool_name,
            args,
            kind,
        }
    }

    pub fn summary(&self) -> String {
        match &self.kind {
            ApprovalKind::Shell { command } => format!("{} {command}", self.tool_name),
            ApprovalKind::FileChange { path } => format!("{} {path}", self.tool_name),
            ApprovalKind::Generic => format!("{} {}", self.tool_name, self.args),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum SessionApproval {
    Shell(String),
    FileChange(String),
    Tool(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalDecision {
    Approve,
    ApproveForSession,
    Deny { reason: String },
    Cancel,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ApprovalOutcome {
    pub frame: ClientFrame,
    pub remembered: Option<SessionApproval>,
}

#[derive(Debug, Clone, Default)]
pub struct ApprovalState {
    pending: Option<PendingApproval>,
    session_approvals: HashSet<SessionApproval>,
}

impl ApprovalState {
    pub fn pending(&self) -> Option<&PendingApproval> {
        self.pending.as_ref()
    }

    pub fn set_pending(&mut self, pending: PendingApproval) {
        self.pending = Some(pending);
    }

    pub fn decide(&mut self, decision: ApprovalDecision) -> Option<ApprovalOutcome> {
        let pending = self.pending.take()?;
        let (approved, reason, remembered) = match decision {
            ApprovalDecision::Approve => (true, None, None),
            ApprovalDecision::ApproveForSession => {
                let remembered = session_rule_for(&pending);
                if let Some(rule) = &remembered {
                    self.session_approvals.insert(rule.clone());
                }
                (true, None, remembered)
            }
            ApprovalDecision::Deny { reason } => (false, Some(reason), None),
            ApprovalDecision::Cancel => (false, Some("cancelled by user".to_string()), None),
        };
        Some(ApprovalOutcome {
            frame: ClientFrame::ApprovalResponse {
                run_id: pending.run_id,
                tool_call_id: pending.tool_call_id,
                approved,
                reason,
            },
            remembered,
        })
    }

    pub fn is_session_approved(&self, approval: &SessionApproval) -> bool {
        self.session_approvals.contains(approval)
    }

    pub fn reconcile_after_reconnect(&mut self, active_run_ids: &[i64]) {
        if let Some(pending) = &self.pending {
            if !active_run_ids.contains(&pending.run_id) {
                self.pending = None;
            }
        }
    }
}

fn classify_approval(tool_name: &str, args: &Value) -> ApprovalKind {
    if tool_name == "bash" {
        return ApprovalKind::Shell {
            command: args
                .get("command")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        };
    }
    if matches!(tool_name, "write_file" | "edit_file" | "multi_edit") {
        return ApprovalKind::FileChange {
            path: args
                .get("file_path")
                .or_else(|| args.get("path"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        };
    }
    ApprovalKind::Generic
}

fn session_rule_for(pending: &PendingApproval) -> Option<SessionApproval> {
    match &pending.kind {
        ApprovalKind::Shell { command } => Some(SessionApproval::Shell(command.clone())),
        ApprovalKind::FileChange { path } => Some(SessionApproval::FileChange(path.clone())),
        ApprovalKind::Generic => Some(SessionApproval::Tool(pending.tool_name.clone())),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::protocol::ClientFrame;

    fn shell_pending() -> PendingApproval {
        PendingApproval::new(
            42,
            "call-1".to_string(),
            "bash".to_string(),
            json!({"command":"git status"}),
        )
    }

    #[test]
    fn pending_approval_classifies_shell_file_and_generic_tools() {
        assert_eq!(
            shell_pending().kind,
            ApprovalKind::Shell {
                command: "git status".to_string()
            }
        );
        assert_eq!(
            PendingApproval::new(
                1,
                "call-2".to_string(),
                "write_file".to_string(),
                json!({"file_path":"src/main.rs"}),
            )
            .kind,
            ApprovalKind::FileChange {
                path: "src/main.rs".to_string()
            }
        );
        assert!(matches!(
            PendingApproval::new(1, "call-3".to_string(), "ask_user".to_string(), json!({})).kind,
            ApprovalKind::Generic
        ));
    }

    #[test]
    fn decisions_create_approval_response_frames_and_clear_pending() {
        let mut approvals = ApprovalState::default();
        approvals.set_pending(shell_pending());

        let outcome = approvals.decide(ApprovalDecision::Deny {
            reason: "not now".to_string(),
        });

        assert_eq!(
            outcome.unwrap().frame,
            ClientFrame::ApprovalResponse {
                run_id: 42,
                tool_call_id: "call-1".to_string(),
                approved: false,
                reason: Some("not now".to_string()),
            }
        );
        assert!(approvals.pending().is_none());
    }

    #[test]
    fn accept_for_session_remembers_shell_rule() {
        let mut approvals = ApprovalState::default();
        approvals.set_pending(shell_pending());

        let outcome = approvals
            .decide(ApprovalDecision::ApproveForSession)
            .unwrap();

        assert_eq!(
            outcome.remembered,
            Some(SessionApproval::Shell("git status".to_string()))
        );
        assert!(approvals.is_session_approved(&SessionApproval::Shell("git status".to_string())));
    }

    #[test]
    fn cancel_is_a_denial_with_cancel_reason() {
        let mut approvals = ApprovalState::default();
        approvals.set_pending(shell_pending());

        let outcome = approvals.decide(ApprovalDecision::Cancel).unwrap();

        assert_eq!(
            outcome.frame,
            ClientFrame::ApprovalResponse {
                run_id: 42,
                tool_call_id: "call-1".to_string(),
                approved: false,
                reason: Some("cancelled by user".to_string()),
            }
        );
    }

    #[test]
    fn pending_approval_disappears_after_reconnect_when_run_is_absent() {
        let mut approvals = ApprovalState::default();
        approvals.set_pending(shell_pending());

        approvals.reconcile_after_reconnect(&[7, 8]);

        assert!(approvals.pending().is_none());
    }
}
