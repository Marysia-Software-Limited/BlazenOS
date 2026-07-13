"""Tests for the portable mesh LLM client + first-reachable resolver.

A fake OpenAI-compatible server (stdlib) stands in for rachel's ``mlx_lm.server``
so the wire format + fallback logic are exercised without a real model.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mesh_registry import Mesh

from mesh_llm import MeshLlm, MeshLlmError, pick


class _Handler(BaseHTTPRequestHandler):
    reply = "Dzień dobry!"
    models_status = 200

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(self.server.models_status)  # type: ignore[attr-defined]
            self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        # Echo the number of messages so tests can assert history was sent.
        content = f"{self.server.reply} [{len(req.get('messages', []))}]"  # type: ignore[attr-defined]
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def _serve(reply="Dzień dobry!", models_status=200):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.reply = reply  # type: ignore[attr-defined]
    httpd.models_status = models_status  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def test_available_true_when_models_probe_ok():
    httpd, url = _serve()
    try:
        assert MeshLlm(url, model="m").available is True
    finally:
        httpd.shutdown()


def test_available_false_when_unreachable():
    assert MeshLlm("http://127.0.0.1:1", model="m").available is False


def test_chat_sends_full_history_and_returns_reply():
    httpd, url = _serve(reply="Cześć")
    try:
        out = MeshLlm(url, model="bielik").chat(
            [{"role": "system", "content": "Jesteś Dżesika."},
             {"role": "user", "content": "Cześć"}])
        assert out == "Cześć [2]"  # 2 messages reached the server
    finally:
        httpd.shutdown()


def test_chat_raises_on_unreachable():
    with pytest.raises(MeshLlmError):
        MeshLlm("http://127.0.0.1:1", model="m").chat([{"role": "user", "content": "x"}])


def _mesh_with(url):
    return Mesh({"self": "rachel", "nodes": {"rachel": {"host": "127.0.0.1", "resources": {
        "llm": {"mlx-bielik-11b": {"kind": "openai", "url": url, "model": "bielik"}}}}}})


def test_pick_returns_first_reachable():
    httpd, url = _serve()
    try:
        client = pick(["absent", "mlx-bielik-11b"], mesh=_mesh_with(url))
        assert client is not None and client.model == "bielik"
    finally:
        httpd.shutdown()


def test_pick_skips_unreachable_and_returns_none():
    mesh = _mesh_with("http://127.0.0.1:1")  # advertised but down
    assert pick(["mlx-bielik-11b"], mesh=mesh) is None


def test_from_resource_reads_model_attr():
    httpd, url = _serve()
    try:
        res = _mesh_with(url).resource("llm", "mlx-bielik-11b")
        assert MeshLlm.from_resource(res).model == "bielik"
    finally:
        httpd.shutdown()
