"""Streamed LLM text to speakable sentences. A pure function of the text: no I/O, no clock.

``SentenceSplitter.feed(chunk)`` takes the next piece of the stream (any size, even one character)
and returns the sentences that are now complete; ``flush()`` returns the rest at the end. The
result does not depend on where the chunk boundaries fall: a sentence ends at ``. ! ?`` (with
closing quotes or brackets) followed by whitespace, so "3.5" and "e.g. this" never split, and a
sentence is only released once the character after its punctuation has arrived.

Every sentence is cleaned for the TTS: markdown, emoji and newlines are removed. A sentence longer
than ``max_chars`` is split at the last clause boundary (comma, semicolon, colon, or before
" and " / " but " / " or " / " so ") that fits, else at the last space, else hard.
"""

from __future__ import annotations

import re
import unicodedata

import config

_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "mt", "e.g", "i.e",
                  "approx", "inc", "ltd", "co"}
_CLOSERS = "*_\"')]”’"  # may follow the sentence punctuation
_CLAUSE_END = re.compile(r"[,;:]\s")
_CLAUSE_WORD = re.compile(r"\s(?:and|but|or|so)\s")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LEADING_MARK = re.compile(r"^(?:#{1,6}\s*|>\s*|[-*•]\s+)+")
_INNER_MARK = re.compile(r"\s[-*•]\s")
_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")
_SPACES = re.compile(r"\s+")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF←-⇿⌀-⏿☀-➿⬀-⯿"
    "︀-️‍⃣\U000E0000-\U000E007F]"
)


def clean_text(raw: str) -> str:
    """Make *raw* safe to speak: no markdown, no emoji, one line, single spaces."""
    text = _LINK.sub(r"\1", raw.replace("\r", " ").replace("\n", " "))
    text = text.replace("`", "").replace("*", "")
    text = _UNDERSCORE.sub("", text)
    text = _EMOJI.sub("", text)
    text = "".join(c for c in text if unicodedata.category(c) not in ("So", "Cs", "Co"))
    text = _SPACES.sub(" ", text).strip()
    text = _LEADING_MARK.sub("", text)
    text = _INNER_MARK.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


class SentenceSplitter:
    """Chunks in, sentences out (see the module docstring)."""

    def __init__(self, max_chars: int = config.CHAT_MAX_SENTENCE_CHARS) -> None:
        self.max_chars = max_chars
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk.replace("\r", " ").replace("\n", " ")
        return self._drain()

    def flush(self) -> list[str]:
        """The end of the stream: release everything that is left."""
        self._buffer += " "  # a virtual whitespace after the last punctuation
        sentences = self._drain()
        rest = clean_text(self._buffer)
        self._buffer = ""
        if _speakable(rest):
            sentences.append(rest)
        return sentences

    def reset(self) -> None:
        self._buffer = ""

    # -- internals ---------------------------------------------------------------------------
    def _drain(self) -> list[str]:
        out: list[str] = []
        while True:
            end = self._boundary()
            if end is not None and end <= self.max_chars:
                raw, self._buffer = self._buffer[:end], self._buffer[end:].lstrip()
            elif len(self._buffer) > self.max_chars:
                cut = self._clause_cut()
                raw, self._buffer = self._buffer[:cut], self._buffer[cut:].lstrip()
            else:
                return out
            cleaned = clean_text(raw)
            if _speakable(cleaned):
                out.append(cleaned)

    def _boundary(self) -> int | None:
        """End index (exclusive, before the whitespace) of the first complete sentence."""
        text = self._buffer
        i = 0
        while i < len(text):
            if text[i] not in ".!?":
                i += 1
                continue
            j = i
            while j + 1 < len(text) and text[j + 1] in ".!?":
                j += 1
            while j + 1 < len(text) and text[j + 1] in _CLOSERS:
                j += 1
            if j + 1 >= len(text):
                return None  # the next character has not arrived yet
            if text[j + 1].isspace() and not self._is_abbreviation(text, i):
                return j + 1
            i = j + 1
        return None

    @staticmethod
    def _is_abbreviation(text: str, dot: int) -> bool:
        if text[dot] != ".":
            return False
        start = dot
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        word = text[start:dot].strip("*_\"'([").lower()
        if word in _ABBREVIATIONS:
            return True
        return len(word) == 1 and word.isalpha() and text[start:dot].strip("*_\"'([").isupper()

    def _clause_cut(self) -> int:
        """Where to cut an overlong sentence: a clause boundary, else a space, else the limit."""
        head = self._buffer[: self.max_chars]
        cuts = [m.end() for m in _CLAUSE_END.finditer(head)]
        cuts += [m.start() for m in _CLAUSE_WORD.finditer(head)]
        cuts = [c for c in cuts if c > 0]
        if cuts:
            return max(cuts)
        space = head.rfind(" ")
        return space if space > 0 else self.max_chars


def _speakable(text: str) -> bool:
    return any(c.isalnum() for c in text)


def split_all(text: str, max_chars: int = config.CHAT_MAX_SENTENCE_CHARS) -> list[str]:
    """The sentences of a complete text (feed once, then flush)."""
    splitter = SentenceSplitter(max_chars)
    return splitter.feed(text) + splitter.flush()
