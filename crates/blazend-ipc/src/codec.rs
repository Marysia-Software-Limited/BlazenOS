//! Length-prefixed JSON framing for the IPC wire format.

use bytes::{BufMut, BytesMut};
use serde::{Serialize, de::DeserializeOwned};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::{IpcError, Result};

/// Maximum allowed frame size (1 MiB). Audio frames stay well under this.
pub const MAX_FRAME_BYTES: usize = 1 << 20;

/// A raw frame: a single length-prefixed JSON message.
#[derive(Debug, Clone)]
pub struct Frame {
    /// Raw JSON payload.
    pub payload: Vec<u8>,
}

impl Frame {
    /// Decode the payload as `T`.
    pub fn decode<T: DeserializeOwned>(&self) -> Result<T> {
        Ok(serde_json::from_slice(&self.payload)?)
    }
}

/// Encoder / decoder for [`Frame`]s on an async byte stream.
pub struct FrameCodec;

impl FrameCodec {
    /// Read one frame from the stream. Returns `None` on clean EOF.
    pub async fn read_one<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Option<Frame>> {
        let mut len_buf = [0u8; 4];
        match reader.read_exact(&mut len_buf).await {
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(IpcError::Io(e)),
        }
        let len = u32::from_be_bytes(len_buf) as usize;
        if len > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge(len));
        }
        let mut buf = vec![0u8; len];
        reader.read_exact(&mut buf).await?;
        Ok(Some(Frame { payload: buf }))
    }

    /// Encode and write one value as a single frame.
    pub async fn write_one<W: AsyncWrite + Unpin, T: Serialize>(
        writer: &mut W,
        value: &T,
    ) -> Result<()> {
        let payload = serde_json::to_vec(value)?;
        if payload.len() > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge(payload.len()));
        }
        let mut buf = BytesMut::with_capacity(4 + payload.len());
        buf.put_u32(payload.len() as u32);
        buf.put_slice(&payload);
        writer.write_all(&buf).await?;
        writer.flush().await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;
    use tokio::io::duplex;

    #[derive(Debug, Serialize, Deserialize, PartialEq)]
    struct Ping {
        n: u32,
        msg: String,
    }

    #[tokio::test]
    async fn round_trip() {
        let (mut a, mut b) = duplex(64 * 1024);
        let value = Ping { n: 7, msg: "hi".into() };
        FrameCodec::write_one(&mut a, &value).await.unwrap();
        drop(a);
        let frame = FrameCodec::read_one(&mut b).await.unwrap().unwrap();
        let decoded: Ping = frame.decode().unwrap();
        assert_eq!(decoded, value);
    }

    #[tokio::test]
    async fn clean_eof_returns_none() {
        let (a, mut b) = duplex(64);
        drop(a);
        assert!(FrameCodec::read_one(&mut b).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn rejects_oversized() {
        // Manually craft a length prefix that exceeds MAX_FRAME_BYTES.
        let (mut a, mut b) = duplex(64);
        let bogus_len = (MAX_FRAME_BYTES as u32 + 1).to_be_bytes();
        a.write_all(&bogus_len).await.unwrap();
        drop(a);
        let err = FrameCodec::read_one(&mut b).await.unwrap_err();
        assert!(matches!(err, IpcError::FrameTooLarge(_)));
    }

    #[tokio::test]
    async fn near_cap_frame_roundtrips() {
        // A payload close to (but under) the 1 MiB cap should still
        // round-trip cleanly through the codec without truncation.
        #[derive(Debug, Serialize, Deserialize, PartialEq)]
        struct Big {
            blob: String,
        }
        let blob = "X".repeat(900_000);
        let original = Big { blob };
        let (mut a, mut b) = duplex(2 * 1024 * 1024);
        FrameCodec::write_one(&mut a, &original).await.unwrap();
        drop(a);
        let frame = FrameCodec::read_one(&mut b).await.unwrap().unwrap();
        let decoded: Big = frame.decode().unwrap();
        assert_eq!(decoded, original);
    }
}
