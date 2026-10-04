"""The one real-LLM check: a running Ollama with the configured model pulled. Excluded by default
(marker ``llm``): ``pytest -m llm -s tests/test_chat_llm.py``. It heats the laptop; run it alone.
It prints the streamed reply and the timings (first token, tokens per second)."""

from __future__ import annotations

import time

import pytest
import requests

import config
from brain.chat import ChatError, OllamaChat
from brain.sentences import split_all


@pytest.mark.llm
def test_real_ollama_streams_a_short_reply() -> None:
    chat = OllamaChat(max_tokens=30)
    try:
        requests.get(f"{chat.url}/api/version", timeout=3).raise_for_status()
    except requests.RequestException as error:
        pytest.skip(f"no Ollama at {chat.url}: {error}")
    assert chat.warm_up(), "the model could not be loaded"  # a cold load can exceed the timeout
    messages = [{"role": "system", "content": config.CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": "Say hello and tell me who you are."}]
    started = time.perf_counter()
    first = None
    chunks = []
    try:
        for piece in chat.stream(messages):
            if first is None:
                first = time.perf_counter() - started
            chunks.append(piece)
    except ChatError as error:
        pytest.skip(f"the model is not usable here: {error}")
    total = time.perf_counter() - started
    text = "".join(chunks)
    print(f"\nmodel {chat.model}: first chunk after {first:.2f} s, {len(chunks)} chunks in "
          f"{total:.1f} s ({len(chunks) / total:.1f} chunks/s)")
    print(f"reply: {text!r}\nsentences: {split_all(text)}")
    assert len(chunks) > 1 and text.strip()  # a streamed reply, not one blob
    assert not chat.connection_open
