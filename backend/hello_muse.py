"""Day 1 check: can we reach Muse Spark? Run: uv run hello_muse.py"""

from llm import MODEL, chat

reply = chat([{"role": "user", "content": "Say hello to FlowGuard AI in one sentence."}])
print(f"[{MODEL}] {reply}")
