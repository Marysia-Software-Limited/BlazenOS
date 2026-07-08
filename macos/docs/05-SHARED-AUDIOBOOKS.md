# 05 — Accessing the shared audiobooks from rachel (macOS)

paul (the GPU node) renders Calibre ebooks to Polish audiobooks with XTTS and
**shares them over the mesh**. rachel (and the Pi) don't re-render — they stream
paul's chapters and resolve titles from a shared catalog. This is the same
`media` mesh resource + catalog publish/merge the Pi uses; here's how rachel taps
it.

## The mechanism (already built)

- **paul renders + keeps** each book as per-chapter MP3s under
  `~/audiobooks/<slug>/NNN.mp3` and updates `catalog.json`
  (`linux/agent/.../books.py` `render_to_files`; batch:
  `scripts/render-literatura.py`).
- **paul serves** the library over HTTP — `jessica --serve-media` on **:7477**
  (`jessica-media.service`), advertised in [`../../configs/mesh.yaml`](../../configs/mesh.yaml)
  as paul's `media` resource:
  ```yaml
  paul:
    resources:
      media:
        audiobooks: { kind: http, url: "http://192.168.50.102:7477/" }
  ```
- **`GET /catalog.json`** returns the catalog with chapters rewritten to absolute
  URLs (`http://192.168.50.102:7477/<slug>/NNN.mp3`), and only books paul actually
  holds. **`GET /<slug>/NNN.mp3`** streams a chapter.

## What rachel needs to do

1. **Discover** paul's media endpoint from the mesh (don't hardcode the IP) —
   `macos/agent` already loads the registry (`from mesh_registry import Mesh`):
   ```python
   from mesh_registry import Mesh
   media = Mesh.load().resource("media", "audiobooks")   # → paul's :7477 URL
   ```
2. **Merge the shared catalog** into rachel's local one (same logic as the Pi's
   `jessica --pull-catalog`): fetch `media.url + "catalog.json"` and upsert its
   books by `slug` into rachel's audiobook catalog. Books rendered on paul then
   resolve on rachel by title. (Reuse `books.pull_catalog` if rachel imports the
   `jessica_linux` helper, or port the ~15-line merge.)
3. **Play** a chapter by streaming its URL. `macos/player` (`rachel-player`, cpal)
   already takes a source argument and supports the speech compressor — pass the
   `http://…/NNN.mp3` URL as the chapter, exactly as the Pi's `blazend-player`
   does. Auto-advance through `chapters[]`; save position with the shared
   `AudiobookProgress` (`domains/audiobook-catalog`) so it syncs via the fabric.

## Verify (from the Mac, once paul is serving)

```sh
# paul's shared catalog (what rachel can resolve + stream)
curl -s http://192.168.50.102:7477/catalog.json | python3 -m json.tool | head
# a chapter is reachable
curl -sI http://192.168.50.102:7477/calibre-35/001.mp3 | head -1   # → 200
# rachel-player streams it (speech compressor on)
rachel-player "http://192.168.50.102:7477/calibre-35/001.mp3" --compress
```

## Status (2026-07-08)
- **HTTP streaming is live in the shared engine.** `domains/blazend-audiobook`'s
  `open_media()` routes `http(s)://` sources through a streaming `HttpMediaSource`
  (paul's stdlib server has no `Range`, so seek is emulated: forward read-and-
  discard, backward re-fetch) — single-file books stream in O(1) memory. Both
  `rachel-player` and (after Phase C) the Pi's player get it.
- **Automatic pull + fabric run under launchd** on rachel:
  `macos/launchd/org.blazen.pull-catalog.plist` (15-min catalog merge) and
  `org.blazen.fabric.plist` (KeepAlive; serves `:7475/fabric/snapshot` so progress
  + notes sync). Both point at `macos/agent/.venv`.

## Notes
- **Strict-improvement:** if paul is off, the stream just isn't available — nothing
  else breaks. rachel keeps any books it rendered/holds locally.
- **Progress is shared:** a book's position (`AudiobookProgress`, keyed by slug)
  rides `blazend-fabric`, so resume works across the Pi / paul / rachel.
- **Reference consumers:** the Pi's catalog merge is `jessica --pull-catalog`
  (`linux/agent/.../books.py` `pull_catalog`); the publish side is `serve_media` /
  `published_catalog`. Integration tests live at
  `linux/agent/tests/integration/test_shared_resources.py`
  (`BLAZEN_INTEGRATION=1 make test-integration`).
