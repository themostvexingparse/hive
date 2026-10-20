"""A scripted stand-in for the OpenAI Responses API, so the real `codex` CLI can be driven in
tests without an account: point a Codex model provider's base_url at it.

Each POST /v1/responses answers with the next turn of the script (a list of output items),
streamed as server-sent events; when the script runs out it answers with a short message.
Every request body is kept in `requests`.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def message(text: str) -> dict:
    return {"type": "message", "role": "assistant", "id": "msg",
            "content": [{"type": "output_text", "text": text}]}


def mcp_call(call_id: str, tool: str, **args) -> dict:
    """A call of a tool of the `hive` MCP server (Codex namespaces them per server)."""
    return {"type": "function_call", "call_id": call_id, "namespace": "mcp__hive", "name": tool,
            "arguments": json.dumps(args)}


def patch(call_id: str, text: str) -> dict:
    return {"type": "custom_tool_call", "call_id": call_id, "name": "apply_patch", "input": text}


def tool_outputs(requests: list[dict]) -> dict[str, str]:
    """{call_id: output} for every tool result Codex sent back to the model."""
    out = {}
    for body in requests:
        for item in body.get("input", []):
            if str(item.get("type", "")).endswith("call_output"):
                out[item["call_id"]] = str(item.get("output"))
    return out


class FakeResponses:
    def __init__(self, script: list[list[dict]]):
        self.script = list(script)
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"data": [], "models": []}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                if not self.path.endswith("/responses"):
                    self.send_response(404)
                    self.end_headers()
                    return
                fake.requests.append(json.loads(raw or b"{}"))
                items = fake.script.pop(0) if fake.script else [message("Nothing to do.")]
                rid = f"resp_{len(fake.requests)}"
                events = [{"type": "response.created", "response": {"id": rid}}]
                events += [{"type": "response.output_item.done", "item": it} for it in items]
                events.append({"type": "response.completed", "response": {"id": rid, "usage": {
                    "input_tokens": 1, "input_tokens_details": None, "output_tokens": 1,
                    "output_tokens_details": None, "total_tokens": 2}}})
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for ev in events:
                    self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def codex_config(self, model: str = "gpt-6-astra") -> str:
        """A config.toml for a CODEX_HOME that sends every model request here."""
        return (f'model_provider = "fake"\nmodel = "{model}"\n\n[model_providers.fake]\n'
                f'name = "fake"\nbase_url = "{self.url}"\nwire_api = "responses"\n')

    def close(self):
        self.server.shutdown()
