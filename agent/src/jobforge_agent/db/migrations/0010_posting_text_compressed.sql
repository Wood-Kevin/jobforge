-- 0010_posting_text_compressed: posting text stored zlib-compressed (P1.10, storage at built-in-list scale).
-- New raw_postings rows put the description in these columns (db/textpack.py) and leave description_raw and
-- description_text NULL; rows written before 0010 keep their plain text (the table is append-only, so they are
-- never rewritten). Readers use coalesce(description_text, unpack_text(description_text_z)); unpack_text is
-- registered on every connection by db.connect.open_connection. A row holds one copy of each, never both.
-- Also from 0010: payload is stored as the JSON literal null (the API object was never read; payload_sha256
-- still records it and drives change detection).
ALTER TABLE raw_postings ADD COLUMN description_raw_z BLOB CHECK (description_raw_z IS NULL OR description_raw IS NULL);
ALTER TABLE raw_postings ADD COLUMN description_text_z BLOB CHECK (description_text_z IS NULL OR description_text IS NULL);
