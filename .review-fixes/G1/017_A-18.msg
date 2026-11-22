fix(ffmpeg): re-read checksums once on mismatch from mutable latest

Checksums and archive come from the mutable releases/latest, fetched in two
requests. If upstream republished in between, the install failed with a
spurious checksum mismatch. Re-download checksums.sha256 once on mismatch
and compare again; a second mismatch still fails. Pinning a dated tag with
embedded SHA256 values is left for a maintainer-run release bump.

Refs: A-18
