"""Remove credentials from text that may be stored or sent onward.

DuckDB 1.5.6 repeats the catalog URI, password included, when attaching the
lake fails. Dagster records that text on the run, and the failure sensor posts
it to Healthchecks.io. The host and the error stay; the secret does not.
"""

import re

# postgresql://user:secret@host -> postgresql://user:***@host
# The password runs up to '@' so a slash inside it is still covered.
_USERINFO = re.compile(r"(://[^\s/:@]+:)([^@\s]+)@")
# libpq keyword form: password=secret
_PASSWORD_PARAM = re.compile(r"(?i)(\bpassword\s*=\s*)(\S+)")
# DuckDB: SECRET 'value' / KEY_ID 'value' inside CREATE SECRET.
_SQL_LITERAL = re.compile(r"(?i)(\b(?:SECRET|KEY_ID)\s+')([^']*)(')")


def redact_secrets(text: str) -> str:
    """Replace credential material in `text`. A string with none is unchanged."""
    text = _USERINFO.sub(r"\1***@", text)
    text = _SQL_LITERAL.sub(r"\1***\3", text)
    text = _PASSWORD_PARAM.sub(r"\1***", text)
    return text
