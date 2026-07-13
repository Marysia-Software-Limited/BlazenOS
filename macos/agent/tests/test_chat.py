import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mesh_registry import Mesh

from rachel.chat import RachelChat, classify, persona, recall_context


def test_classify_routes_recommend_and_command():
    assert classify("poleć mi książkę") == "recommend"
    assert classify("dlaczego niebo jest niebieskie") == "recommend"
    assert classify("która godzina") == "command"


def test_persona_falls_back_without_config(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_LLM_CONFIG", str(tmp_path / "nope.yaml"))
    assert "Dżesika" in persona()


def test_recall_context_summarises_recent_notes(tmp_path):
    p = tmp_path / "memory.json"
    p.write_text(json.dumps({"notes": [{"text": "kup mleko"}, {"text": "oddaj książkę"}]}),
                 encoding="utf-8")
    out = recall_context(memory_path=p, limit=5)
    assert "kup mleko" in out and "oddaj książkę" in out


def test_recall_context_empty_when_no_notes(tmp_path):
    assert recall_context(memory_path=tmp_path / "absent.json") == ""


class _Echo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        last = req["messages"][-1]["content"]
        body = json.dumps({"choices": [{"message": {"content": f"echo: {last}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def _serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def _mesh(url):
    return Mesh({"self": "rachel", "nodes": {"rachel": {"host": "127.0.0.1", "resources": {
        "llm": {"mlx-bielik-11b": {"kind": "openai", "url": url, "model": "bielik"}}}}}})


def test_ask_routes_to_mesh_backend_and_keeps_history():
    httpd, url = _serve()
    try:
        chat = RachelChat(mesh=_mesh(url))
        assert chat.ask("cześć") == "echo: cześć"
        assert len(chat._history) == 2  # noqa: SLF001 — user + assistant recorded
    finally:
        httpd.shutdown()


def test_ask_graceful_when_no_brain_reachable():
    chat = RachelChat(mesh=_mesh("http://127.0.0.1:1"))
    out = chat.ask("cześć")
    assert "nie mam dostępu" in out
