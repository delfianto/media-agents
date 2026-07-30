"""Shared, dependency-free helpers used by every skill's CLI in this repo
(av1transcode, trackstrip, organize, artwork, subtitle): directory walking, `.env`
parsing, library-root auto-detection, and human-readable byte formatting.
Factored out after the same directory-walk bug (a live `os.walk` picking up
its own freshly-written output as a new source mid-run) was found fixed in
one skill's hand-rolled walker but still latent in another's -- see
skills/av1-transcode/reference/incidents.md.
"""
