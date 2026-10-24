-- 0010_posting_text_compressed: posting text moves out of raw_postings into posting_texts, zlib-compressed,
-- and is kept only while it is wanted (P1.10, storage at built-in-list scale; Kevin: "no reason to keep posts
-- that just rejected and don't meet filters").
-- - raw_postings stays append-only and keeps every posting's identity, URLs and hashes (change detection,
--   closure, filter verdicts). New rows leave description_raw / description_text NULL and store payload as the
--   JSON literal null (the API object was never read; payload_sha256 still records it).
-- - posting_texts holds the description of a posting version (db/textpack.py). Discovery deletes it when the
--   posting is rejected by the search profile without an "evaluate anyway" override, and stores it again from
--   a later full fetch if the posting is wanted again (profile change, override). No search profile yet:
--   nothing has passed, so nothing is kept.
-- - Rows written before 0010 keep their plain text in raw_postings (never rewritten). Readers use
--   coalesce(rp.description_text, unpack_text(pt.description_text_z)) with a LEFT JOIN on posting_texts;
--   unpack_text is registered on every connection by db.connect.open_connection.
CREATE TABLE posting_texts (
    raw_posting_id     INTEGER PRIMARY KEY REFERENCES raw_postings(id),
    description_raw_z  BLOB,
    description_text_z BLOB
) STRICT;
