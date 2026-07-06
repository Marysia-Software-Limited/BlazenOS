import textwrap

from mesh_registry.registry import Mesh

_YAML = textwrap.dedent("""
    version: 1
    self: jessica
    nodes:
      paul:
        role: gpu
        host: 192.168.50.102
        resources:
          llm:
            ollama-11b: { kind: openai, url: "http://192.168.50.102:11434", model: bielik-11b }
          tts:
            xtts: { kind: xtts, url: "http://192.168.50.102:8091/synthesize", language: pl, speaker: "Ana Florence" }
      rachel:
        role: desktop
        host: 192.168.50.186
        resources:
          tts:
            apple-zosia-enhanced: { kind: apple, local: true, voice: "Zosia (Enhanced)" }
""")


def _mesh(tmp_path):
    p = tmp_path / "mesh.yaml"
    p.write_text(_YAML)
    return Mesh.load(str(p))


def test_lists_nodes_and_host(tmp_path):
    m = _mesh(tmp_path)
    assert set(m.nodes) == {"paul", "rachel"}
    assert m.host("paul") == "192.168.50.102"


def test_resolves_xtts_resource(tmp_path):
    m = _mesh(tmp_path)
    r = m.resource("tts", "xtts")
    assert r.node == "paul"
    assert r.url == "http://192.168.50.102:8091/synthesize"
    assert r.kind == "xtts"
    assert r.attrs["speaker"] == "Ana Florence"
    assert not r.local


def test_local_resource_has_no_url(tmp_path):
    m = _mesh(tmp_path)
    r = m.resource("tts", "apple-zosia-enhanced")
    assert r.local and r.url is None
    assert r.attrs["voice"] == "Zosia (Enhanced)"


def test_resources_across_nodes_and_self(tmp_path):
    m = _mesh(tmp_path)
    tts = {r.name for r in m.resources("tts")}
    assert tts == {"xtts", "apple-zosia-enhanced"}
    assert m.self_node == "jessica"
    assert m.resource("tts", "nope") is None


def test_missing_file_is_empty(tmp_path):
    m = Mesh.load(str(tmp_path / "nope.yaml"))
    # falls back to the repo default if present; at minimum must not raise
    assert isinstance(m.nodes, list)
