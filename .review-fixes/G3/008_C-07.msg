fix(config): ignore inline comments in .env values

parse_dotenv kept everything after "=", so KEY=abc  # note gave "abc  # note"
and KEY="abc" # note kept the quotes because the value no longer ended with
one; both produced wrong API keys. Unquoted values now end at whitespace+#,
and quoted values end at their closing quote with a trailing comment ignored.

Refs: C-07
