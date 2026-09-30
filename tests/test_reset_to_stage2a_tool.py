import importlib.util
import json
import sqlite3
from pathlib import Path


def load_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "reset_to_stage2a.py"
    spec = importlib.util.spec_from_file_location("reset_to_stage2a_tool", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reset_database_preserves_stage1_and_clears_downstream(tmp_path):
    tool = load_tool()
    db = tmp_path / "jobs.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT, output_filename TEXT)")
        conn.execute("CREATE TABLE postprocess_jobs (id INTEGER PRIMARY KEY, conversion_job_id INTEGER)")
        conn.execute("CREATE TABLE verification_jobs (id INTEGER PRIMARY KEY, postprocess_job_id INTEGER)")
        conn.execute("CREATE TABLE review_assistant_jobs (id INTEGER PRIMARY KEY, postprocess_job_id INTEGER)")
        conn.executemany("INSERT INTO jobs(id,status,output_filename) VALUES (?,?,?)", [(1,"completed","a.zip"),(2,"failed",None)])
        conn.executemany("INSERT INTO postprocess_jobs VALUES (?,?)", [(10,1),(11,2)])
        conn.executemany("INSERT INTO verification_jobs VALUES (?,?)", [(20,10),(21,11)])
        conn.execute("INSERT INTO review_assistant_jobs VALUES (?,?)", (30,10))
    before = tool.reset_database(db)
    assert before == {"jobs": 2, "postprocess_jobs": 2, "verification_jobs": 2, "review_assistant_jobs": 1}
    assert tool.db_counts(db) == {"jobs": 2, "postprocess_jobs": 0, "verification_jobs": 0, "review_assistant_jobs": 0}


def test_colab_only_registry_preserves_worker_and_pauses_locals(tmp_path):
    tool = load_tool()
    data = tmp_path / "data"
    (data / "colab_workers").mkdir(parents=True)
    registry = {
        "local": {"pi5": {"paused": False, "artifact_enabled": True}, "oneplus": {"paused": False, "artifact_enabled": True}},
        "colab_workers": [{"id":"colab-1","name":"C1","enabled":True,"paused":True,"url":"https://x.trycloudflare.com","model":"koboldcpp","artifact_enabled":False,"remove_requested":False}],
        "review": {"enabled": False, "text_worker_ids": [], "vision_worker_ids": []},
    }
    (data / "worker_registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (data / "colab_workers" / "colab-1.key").write_text("ascii-key", encoding="utf-8")
    loaded, chosen = tool.read_registry(data)
    assert chosen["id"] == "colab-1"
    tool.write_registry_colab_only(data, loaded, "colab-1")
    revised = json.loads((data / "worker_registry.json").read_text(encoding="utf-8"))
    assert revised["local"]["pi5"]["paused"] is True
    assert revised["local"]["oneplus"]["paused"] is True
    assert revised["colab_workers"][0]["paused"] is False
    assert revised["colab_workers"][0]["artifact_enabled"] is True


def test_replace_yaml_scalar(tmp_path):
    tool = load_tool()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("text_verifier_provider: pi5\nvision_verifier_provider: oneplus\n", encoding="utf-8")
    assert tool.replace_yaml_scalar(cfg, "text_verifier_provider", "colab")
    assert tool.replace_yaml_scalar(cfg, "vision_verifier_provider", "colab")
    text = cfg.read_text(encoding="utf-8")
    assert "text_verifier_provider: colab" in text
    assert "vision_verifier_provider: colab" in text
