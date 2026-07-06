"""rachel — the macOS (Apple-Silicon) node's audiobook agent.

Turns Polish Calibre ebooks into chapterized audiobooks rendered with Apple
on-device TTS (Azure Neural as a premium opt-in), written into the shared
audiobook catalog schema and played on the Mac with resume + auto-advance.
Device-independent audiobook logic is imported from the shared
``domains/audiobook-catalog`` lib.
"""
