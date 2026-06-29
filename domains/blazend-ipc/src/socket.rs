//! Unix-socket publisher / subscriber helpers.

use std::path::{Path, PathBuf};

use tokio::net::{UnixListener, UnixStream};
use tokio::sync::mpsc;

use crate::{codec::Frame, events::EventEnvelope, FrameCodec, Result};

/// Default runtime directory for IPC sockets (`/run/blazen/` in production,
/// `$XDG_RUNTIME_DIR/blazen/` or `/tmp/blazen-<uid>/` in dev-host mode).
pub fn runtime_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("BLAZEN_RUNTIME_DIR") {
        return PathBuf::from(dir);
    }
    if let Ok(xdg) = std::env::var("XDG_RUNTIME_DIR") {
        return Path::new(&xdg).join("blazen");
    }
    // On non-Linux dev hosts (macOS) or in tests.
    let uid = nix_uid();
    PathBuf::from(format!("/tmp/blazen-{uid}"))
}

fn nix_uid() -> u32 {
    // Avoid pulling in the `nix` or `libc` crate just for this — read it
    // from the environment when set, else default to 0 so tests on
    // sandboxed CI don't blow up.
    std::env::var("UID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}

/// A publisher: listens on a Unix socket and broadcasts envelopes to every
/// connected subscriber. The publishing side is `mpsc::Sender<EventEnvelope>`.
pub struct Publisher {
    /// Path to the bound socket.
    pub socket_path: PathBuf,
    tx: mpsc::Sender<EventEnvelope>,
}

impl Publisher {
    /// Bind a Unix-domain socket and start the broadcaster task.
    pub async fn bind(socket_path: impl Into<PathBuf>) -> Result<Self> {
        let socket_path = socket_path.into();
        if let Some(parent) = socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let _ = tokio::fs::remove_file(&socket_path).await;
        let listener = UnixListener::bind(&socket_path)?;
        let (tx, mut rx) = mpsc::channel::<EventEnvelope>(256);

        tokio::spawn(async move {
            let mut subscribers: Vec<UnixStream> = Vec::new();
            loop {
                tokio::select! {
                    accept = listener.accept() => {
                        match accept {
                            Ok((stream, _addr)) => subscribers.push(stream),
                            Err(e) => tracing::warn!(error = %e, "accept failed"),
                        }
                    }
                    envelope = rx.recv() => {
                        let Some(envelope) = envelope else { break };
                        let payload = match serde_json::to_vec(&envelope) {
                            Ok(b) => b,
                            Err(e) => { tracing::error!(error = %e, "encode failed"); continue; }
                        };
                        // Never put a frame on the wire the receiver must
                        // reject: an oversized payload would fail every
                        // subscriber's read and take the consumers down with
                        // it. Drop it here and keep the bus alive.
                        if payload.len() > crate::codec::MAX_FRAME_BYTES {
                            tracing::error!(
                                bytes = payload.len(),
                                topic = envelope.event.topic().as_str(),
                                "dropping oversized event",
                            );
                            continue;
                        }
                        // Frame once (length prefix + payload), then fan out.
                        let mut framed = Vec::with_capacity(4 + payload.len());
                        framed.extend_from_slice(&(payload.len() as u32).to_be_bytes());
                        framed.extend_from_slice(&payload);
                        subscribers.retain_mut(|s| broadcast_one(s, &framed).is_ok());
                    }
                }
            }
        });

        Ok(Self { socket_path, tx })
    }

    /// Publish an envelope to every connected subscriber.
    pub async fn publish(&self, envelope: EventEnvelope) -> Result<()> {
        self.tx.send(envelope).await.map_err(|_| {
            crate::IpcError::Io(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "publisher closed",
            ))
        })
    }
}

fn broadcast_one(stream: &mut UnixStream, framed: &[u8]) -> std::io::Result<()> {
    // Non-blocking single write of the whole frame. `try_write` may write
    // fewer bytes than asked when the kernel send buffer is nearly full; a
    // short write here would split a frame mid-message and desync the peer
    // for good. Treat any partial/blocked write as "can't keep up" and let
    // the caller drop the subscriber (subscribers.retain_mut) — the same
    // backpressure policy as a hard error, but without corrupting the wire.
    match stream.try_write(framed) {
        Ok(n) if n == framed.len() => Ok(()),
        Ok(_) => Err(std::io::Error::new(
            std::io::ErrorKind::WouldBlock,
            "short write — subscriber behind",
        )),
        Err(e) => Err(e),
    }
}

/// A subscriber: connects to a publisher socket and yields envelopes.
pub struct Subscriber {
    stream: UnixStream,
}

impl Subscriber {
    /// Connect to a publisher socket.
    pub async fn connect(socket_path: impl AsRef<Path>) -> Result<Self> {
        let stream = UnixStream::connect(socket_path.as_ref()).await?;
        Ok(Self { stream })
    }

    /// Read the next envelope. `Ok(None)` means clean EOF.
    pub async fn next(&mut self) -> Result<Option<EventEnvelope>> {
        let frame = FrameCodec::read_one(&mut self.stream).await?;
        let Some(Frame { payload }) = frame else {
            return Ok(None);
        };
        let env: EventEnvelope = serde_json::from_slice(&payload)?;
        // Fail-closed on a peer speaking a different protocol version rather
        // than silently mis-decoding it.
        Ok(Some(env.require_current()?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Event;

    #[tokio::test]
    async fn pub_sub_roundtrip() {
        let dir = tempfile_dir();
        let sock = dir.join("test.sock");
        let publisher = Publisher::bind(&sock).await.unwrap();
        // Give the listener a moment.
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;

        let mut subscriber = Subscriber::connect(&sock).await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;

        publisher
            .publish(EventEnvelope::new(
                "test",
                42,
                Event::SystemEvent {
                    kind: "ready".into(),
                    detail: None,
                },
            ))
            .await
            .unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;

        let env = tokio::time::timeout(std::time::Duration::from_secs(1), subscriber.next())
            .await
            .expect("recv timed out")
            .unwrap()
            .unwrap();
        assert_eq!(env.source, "test");
        match env.event {
            Event::SystemEvent { kind, .. } => assert_eq!(kind, "ready"),
            other => panic!("wrong event: {other:?}"),
        }
    }

    #[tokio::test]
    async fn subscriber_rejects_foreign_protocol_version() {
        let dir = tempfile_dir();
        let sock = dir.join("ver.sock");
        let publisher = Publisher::bind(&sock).await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        let mut sub = Subscriber::connect(&sock).await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;

        // A peer stamped with a protocol version this build can't speak.
        let mut env = EventEnvelope::new("test", 1, Event::VadStart);
        env.v = crate::PROTOCOL_VERSION + 1;
        publisher.publish(env).await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;

        let err = tokio::time::timeout(std::time::Duration::from_secs(1), sub.next())
            .await
            .expect("recv timed out")
            .unwrap_err();
        assert!(matches!(err, crate::IpcError::VersionMismatch { .. }));
    }

    fn tempfile_dir() -> PathBuf {
        let d = std::env::temp_dir().join(format!("blazend-ipc-test-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn runtime_dir_honors_env_override() {
        // SAFETY: env mutation is racy across tests; serial_test crate would
        // be the clean fix. We keep this test's footprint small instead.
        let prev = std::env::var("BLAZEN_RUNTIME_DIR").ok();
        // SAFETY: setting env vars is unsafe in Rust 2024 because it can race
        // with concurrent reads. Guarded by the small scope of this test.
        unsafe {
            std::env::set_var("BLAZEN_RUNTIME_DIR", "/tmp/test-runtime");
        }
        assert_eq!(runtime_dir(), PathBuf::from("/tmp/test-runtime"));
        unsafe {
            match prev {
                Some(v) => std::env::set_var("BLAZEN_RUNTIME_DIR", v),
                None => std::env::remove_var("BLAZEN_RUNTIME_DIR"),
            }
        }
    }
}
