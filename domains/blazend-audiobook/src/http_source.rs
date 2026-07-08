//! A streaming [`MediaSource`] over HTTP for playing shared-library chapters
//! straight off a peer's media server (paul's `jessica --serve-media`) without
//! downloading the whole file first.
//!
//! The mesh media server is Python's stdlib `http.server`, which does **not**
//! honour `Range` requests — a ranged GET returns the whole body with `200`.
//! So seek can't use byte ranges. Instead we emulate it: a forward seek
//! read-and-discards from the live body (O(1) memory, no re-fetch); a backward
//! seek re-issues the GET from the start and discards up to the target. Forward
//! playback never seeks back, and resume (`start_seconds`) is one forward seek,
//! so the common paths cost nothing extra. Single-file books (a whole book as
//! one MP3) stream in constant memory rather than buffering hundreds of MB.

use std::io::{self, Read, Seek, SeekFrom};

use symphonia::core::io::MediaSource;

/// A seekable, streaming HTTP body suitable for symphonia.
pub struct HttpMediaSource {
    url: String,
    len: Option<u64>,
    pos: u64,
    reader: Box<dyn Read + Send + Sync>,
}

impl HttpMediaSource {
    /// GET `url` and wrap its body. Fails if the request can't be made.
    pub fn open(url: &str) -> anyhow::Result<Self> {
        let (reader, len) = fetch(url)?;
        Ok(HttpMediaSource {
            url: url.to_string(),
            len,
            pos: 0,
            reader,
        })
    }

    /// Re-issue the GET and rewind our logical position to 0 (backward seek).
    fn reopen(&mut self) -> io::Result<()> {
        let (reader, len) = fetch(&self.url).map_err(to_io)?;
        self.reader = reader;
        self.len = len.or(self.len);
        self.pos = 0;
        Ok(())
    }

    /// Read and throw away `n` bytes, advancing `pos`. Stops early at EOF.
    fn discard(&mut self, mut n: u64) -> io::Result<()> {
        let mut buf = [0u8; 16 * 1024];
        while n > 0 {
            let want = n.min(buf.len() as u64) as usize;
            let got = self.reader.read(&mut buf[..want])?;
            if got == 0 {
                break; // body shorter than the seek target
            }
            self.pos += got as u64;
            n -= got as u64;
        }
        Ok(())
    }
}

fn fetch(url: &str) -> anyhow::Result<(Box<dyn Read + Send + Sync>, Option<u64>)> {
    let resp = ureq::get(url).call()?;
    let len = resp
        .header("Content-Length")
        .and_then(|s| s.trim().parse::<u64>().ok());
    Ok((resp.into_reader(), len))
}

fn to_io(e: anyhow::Error) -> io::Error {
    io::Error::other(e.to_string())
}

impl Read for HttpMediaSource {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let n = self.reader.read(buf)?;
        self.pos += n as u64;
        Ok(n)
    }
}

impl Seek for HttpMediaSource {
    fn seek(&mut self, pos: SeekFrom) -> io::Result<u64> {
        let target: u64 = match pos {
            SeekFrom::Start(o) => o,
            SeekFrom::Current(d) => (self.pos as i64 + d).max(0) as u64,
            SeekFrom::End(d) => {
                let len = self.len.ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::Unsupported,
                        "SeekFrom::End needs a Content-Length",
                    )
                })?;
                (len as i64 + d).max(0) as u64
            }
        };
        if target < self.pos {
            self.reopen()?; // no Range support: rewind = re-fetch from 0
        }
        let ahead = target - self.pos;
        self.discard(ahead)?;
        Ok(self.pos)
    }
}

impl MediaSource for HttpMediaSource {
    fn is_seekable(&self) -> bool {
        true // emulated (forward-discard / rewind-refetch), so always usable
    }

    fn byte_len(&self) -> Option<u64> {
        self.len
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::thread;

    /// A one-file HTTP/1.1 server that, like Python's `http.server`, ignores
    /// `Range` and always returns the full body with `200`. Serves every
    /// connection the same bytes so re-fetch (backward seek) works.
    fn serve(body: Vec<u8>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        thread::spawn(move || {
            for stream in listener.incoming() {
                let mut stream = match stream {
                    Ok(s) => s,
                    Err(_) => continue,
                };
                // Drain the request line + headers (until blank line).
                let mut req = Vec::new();
                let mut byte = [0u8; 1];
                while stream.read(&mut byte).unwrap_or(0) == 1 {
                    req.push(byte[0]);
                    if req.ends_with(b"\r\n\r\n") {
                        break;
                    }
                }
                let header = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nContent-Type: audio/mpeg\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(header.as_bytes());
                let _ = stream.write_all(&body);
                let _ = stream.flush();
            }
        });
        format!("http://{addr}/chapter.mp3")
    }

    fn pattern(n: usize) -> Vec<u8> {
        (0..n).map(|i| (i % 251) as u8).collect()
    }

    #[test]
    fn reads_full_body_and_reports_len() {
        let body = pattern(5000);
        let url = serve(body.clone());
        let mut src = HttpMediaSource::open(&url).unwrap();
        assert_eq!(src.byte_len(), Some(5000));
        let mut got = Vec::new();
        src.read_to_end(&mut got).unwrap();
        assert_eq!(got, body);
    }

    #[test]
    fn forward_seek_discards_without_refetch() {
        let body = pattern(5000);
        let url = serve(body.clone());
        let mut src = HttpMediaSource::open(&url).unwrap();
        assert_eq!(src.seek(SeekFrom::Start(1000)).unwrap(), 1000);
        let mut got = Vec::new();
        src.read_to_end(&mut got).unwrap();
        assert_eq!(got, body[1000..]);
    }

    #[test]
    fn backward_seek_refetches_from_zero() {
        let body = pattern(5000);
        let url = serve(body.clone());
        let mut src = HttpMediaSource::open(&url).unwrap();
        src.seek(SeekFrom::Start(4000)).unwrap();
        assert_eq!(src.seek(SeekFrom::Start(10)).unwrap(), 10);
        let mut got = [0u8; 5];
        src.read_exact(&mut got).unwrap();
        assert_eq!(&got, &body[10..15]);
    }

    #[test]
    fn seek_from_end_uses_content_length() {
        let body = pattern(5000);
        let url = serve(body.clone());
        let mut src = HttpMediaSource::open(&url).unwrap();
        assert_eq!(src.seek(SeekFrom::End(-100)).unwrap(), 4900);
        let mut got = Vec::new();
        src.read_to_end(&mut got).unwrap();
        assert_eq!(got, body[4900..]);
    }
}
