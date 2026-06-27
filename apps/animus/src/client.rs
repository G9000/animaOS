#![allow(dead_code)]

use std::time::Duration;

use anyhow::{bail, Context, Result};
use futures_util::stream::{SplitSink, SplitStream};
use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async, MaybeTlsStream, WebSocketStream};
use url::Url;

use crate::config::AnimusConfig;
use crate::protocol::{
    is_terminal_run_error_code, AuthUser, ClientFrame, ServerFrame, ToolSchema, ToolStatus,
};

const MAX_RECONNECT_DELAY_MS: u64 = 30_000;

type WsStream = WebSocketStream<MaybeTlsStream<TcpStream>>;
type WsWrite = SplitSink<WsStream, Message>;
type WsRead = SplitStream<WsStream>;

#[derive(Debug, Default, Clone)]
pub struct ClientState {
    current_run_id: Option<i64>,
}

impl ClientState {
    pub fn current_run_id(&self) -> Option<i64> {
        self.current_run_id
    }

    pub fn observe_server_frame(&mut self, frame: &ServerFrame) {
        match frame {
            ServerFrame::RunStarted { run_id, .. } => {
                self.current_run_id = Some(*run_id);
            }
            ServerFrame::Cancelled { run_id } if self.current_run_id == Some(*run_id) => {
                self.current_run_id = None;
            }
            ServerFrame::TurnComplete { .. } => {
                self.current_run_id = None;
            }
            ServerFrame::Error { code, .. } if is_terminal_run_error_code(code) => {
                self.current_run_id = None;
            }
            _ => {}
        }
    }

    pub fn user_message(&self, message: impl Into<String>) -> ClientFrame {
        ClientFrame::UserMessage {
            message: message.into(),
        }
    }

    pub fn tool_result(
        &self,
        tool_call_id: impl Into<String>,
        status: ToolStatus,
        result: impl Into<String>,
    ) -> ClientFrame {
        ClientFrame::ToolResult {
            tool_call_id: tool_call_id.into(),
            status,
            result: result.into(),
            stdout: None,
            stderr: None,
        }
    }

    pub fn approval_response(
        &self,
        tool_call_id: impl Into<String>,
        approved: bool,
        reason: Option<String>,
    ) -> Result<ClientFrame> {
        let run_id = self
            .current_run_id
            .context("no active run is available for approval response")?;
        Ok(ClientFrame::ApprovalResponse {
            run_id,
            tool_call_id: tool_call_id.into(),
            approved,
            reason,
        })
    }

    pub fn cancel_current_run(&self) -> Result<ClientFrame> {
        let run_id = self
            .current_run_id
            .context("no active run is available for cancellation")?;
        Ok(ClientFrame::Cancel { run_id })
    }
}

pub struct AnimaWsClient {
    write: WsWrite,
    read: WsRead,
    state: ClientState,
    auth_user: AuthUser,
}

impl AnimaWsClient {
    pub async fn connect(config: &AnimusConfig, tool_schemas: Vec<ToolSchema>) -> Result<Self> {
        let url = agent_ws_url(&config.server_url)?;
        let (stream, _) = connect_async(url.as_str())
            .await
            .with_context(|| format!("failed to connect to {url}"))?;
        let (mut write, mut read) = stream.split();

        send_frame_to(&mut write, &auth_frame(config)).await?;

        let auth_response = read_next_frame(&mut read)
            .await?
            .context("websocket closed before authentication completed")?;
        let auth_user = match &auth_response {
            ServerFrame::AuthOk { user } => user.clone(),
            ServerFrame::Error { message, code } => {
                bail!("authentication failed ({code}): {message}");
            }
            other => {
                bail!("expected auth_ok frame, received {other:?}");
            }
        };

        send_frame_to(
            &mut write,
            &ClientFrame::ToolSchemas {
                tools: tool_schemas,
            },
        )
        .await?;

        Ok(Self {
            write,
            read,
            state: ClientState::default(),
            auth_user,
        })
    }

    pub fn auth_user(&self) -> &AuthUser {
        &self.auth_user
    }

    pub fn current_run_id(&self) -> Option<i64> {
        self.state.current_run_id()
    }

    pub async fn next_frame(&mut self) -> Result<Option<ServerFrame>> {
        let frame = read_next_frame(&mut self.read).await?;
        if let Some(frame) = &frame {
            self.state.observe_server_frame(frame);
        }
        Ok(frame)
    }

    pub async fn send_frame(&mut self, frame: ClientFrame) -> Result<()> {
        send_frame_to(&mut self.write, &frame).await
    }

    pub async fn send_user_message(&mut self, message: impl Into<String>) -> Result<()> {
        self.send_frame(self.state.user_message(message)).await
    }

    pub async fn send_tool_result(
        &mut self,
        tool_call_id: impl Into<String>,
        status: ToolStatus,
        result: impl Into<String>,
    ) -> Result<()> {
        self.send_frame(self.state.tool_result(tool_call_id, status, result))
            .await
    }

    pub async fn send_approval_response(
        &mut self,
        tool_call_id: impl Into<String>,
        approved: bool,
        reason: Option<String>,
    ) -> Result<()> {
        let frame = self
            .state
            .approval_response(tool_call_id.into(), approved, reason)?;
        self.send_frame(frame).await
    }

    pub async fn send_cancel_current_run(&mut self) -> Result<()> {
        let frame = self.state.cancel_current_run()?;
        self.send_frame(frame).await
    }
}

pub fn auth_frame(config: &AnimusConfig) -> ClientFrame {
    if config.unlock_token.is_some() {
        ClientFrame::Auth {
            unlock_token: config.unlock_token.clone(),
            username: config.username.clone(),
            password: None,
        }
    } else {
        ClientFrame::Auth {
            unlock_token: None,
            username: config.username.clone(),
            password: config.password.clone(),
        }
    }
}

pub fn reconnect_delay(attempt: u32) -> Duration {
    let delay = 1_000u64.saturating_mul(2u64.saturating_pow(attempt));
    Duration::from_millis(delay.min(MAX_RECONNECT_DELAY_MS))
}

pub fn agent_ws_url(server_url: &str) -> Result<Url> {
    let mut url = Url::parse(server_url).context("invalid server url")?;
    let scheme = match url.scheme() {
        "http" => "ws",
        "https" => "wss",
        "ws" => "ws",
        "wss" => "wss",
        other => bail!("unsupported server url scheme: {other}"),
    };
    url.set_scheme(scheme)
        .map_err(|_| anyhow::anyhow!("failed to set websocket scheme"))?;

    let current_path = url.path().trim_end_matches('/');
    if current_path.is_empty() {
        url.set_path("/ws/agent");
    } else if current_path != "/ws/agent" {
        url.set_path(&format!("{current_path}/ws/agent"));
    }
    Ok(url)
}

async fn send_frame_to(write: &mut WsWrite, frame: &ClientFrame) -> Result<()> {
    let raw = serde_json::to_string(frame)?;
    write.send(Message::Text(raw)).await?;
    Ok(())
}

async fn read_next_frame(read: &mut WsRead) -> Result<Option<ServerFrame>> {
    while let Some(message) = read.next().await {
        match message? {
            Message::Text(raw) => return Ok(Some(serde_json::from_str(&raw)?)),
            Message::Binary(raw) => return Ok(Some(serde_json::from_slice(&raw)?)),
            Message::Close(_) => return Ok(None),
            Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => {}
        }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::config::AnimusConfig;
    use crate::protocol::{ClientFrame, ServerFrame, ToolStatus};

    #[test]
    fn converts_http_server_url_to_agent_websocket_url() {
        assert_eq!(
            agent_ws_url("http://127.0.0.1:3031").unwrap().as_str(),
            "ws://127.0.0.1:3031/ws/agent"
        );
        assert_eq!(
            agent_ws_url("https://anima.example/ws/agent")
                .unwrap()
                .as_str(),
            "wss://anima.example/ws/agent"
        );
    }

    #[test]
    fn tracks_current_run_id_from_lifecycle_frames() {
        let mut state = ClientState::default();

        state.observe_server_frame(&ServerFrame::RunStarted {
            run_id: 42,
            thread_id: Some(9),
        });
        assert_eq!(state.current_run_id(), Some(42));

        state.observe_server_frame(&ServerFrame::Cancelled { run_id: 42 });
        assert_eq!(state.current_run_id(), None);
    }

    #[test]
    fn clears_current_run_id_after_terminal_agent_error() {
        let mut state = ClientState::default();
        state.observe_server_frame(&ServerFrame::RunStarted {
            run_id: 42,
            thread_id: Some(9),
        });

        state.observe_server_frame(&ServerFrame::Error {
            message: "boom".to_string(),
            code: "AGENT_ERROR".to_string(),
        });

        assert_eq!(state.current_run_id(), None);
        assert!(state.cancel_current_run().is_err());
    }

    #[test]
    fn send_helpers_build_expected_frames_and_use_current_run() {
        let mut state = ClientState::default();
        state.observe_server_frame(&ServerFrame::RunStarted {
            run_id: 42,
            thread_id: None,
        });

        assert_eq!(
            state.user_message("hello"),
            ClientFrame::UserMessage {
                message: "hello".to_string()
            }
        );
        assert_eq!(
            state.tool_result("call-1", ToolStatus::Success, "ok"),
            ClientFrame::ToolResult {
                tool_call_id: "call-1".to_string(),
                status: ToolStatus::Success,
                result: "ok".to_string(),
                stdout: None,
                stderr: None,
            }
        );
        assert_eq!(
            state.approval_response("call-1", true, None).unwrap(),
            ClientFrame::ApprovalResponse {
                run_id: 42,
                tool_call_id: "call-1".to_string(),
                approved: true,
                reason: None,
            }
        );
        assert_eq!(
            state.cancel_current_run().unwrap(),
            ClientFrame::Cancel { run_id: 42 }
        );
    }

    #[test]
    fn reconnect_backoff_is_bounded() {
        assert_eq!(reconnect_delay(0), Duration::from_millis(1_000));
        assert_eq!(reconnect_delay(4), Duration::from_millis(16_000));
        assert_eq!(reconnect_delay(9), Duration::from_millis(30_000));
    }

    #[test]
    fn auth_frame_uses_config_credentials_without_password_when_token_exists() {
        let config = AnimusConfig {
            server_url: "http://127.0.0.1:3031".to_string(),
            workspace: ".".into(),
            unlock_token: Some("token".to_string()),
            username: Some("alice".to_string()),
            password: Some("password".to_string()),
        };

        assert_eq!(
            auth_frame(&config),
            ClientFrame::Auth {
                unlock_token: Some("token".to_string()),
                username: Some("alice".to_string()),
                password: None,
            }
        );
    }
}
