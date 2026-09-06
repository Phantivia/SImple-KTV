import math
import pytest
from pydantic import ValidationError
from app.schemas import JobRequest, TrackPatch
from app.store import Store, Conflict, Missing, uid


@pytest.mark.parametrize("payload", [
    {"task":"effects"},
    {"task":"separate", "model":"denoise"},
    {"task":"export", "command":"rm -rf /"},
    {"task":"erase", "track_id":"../oops", "start":0, "end":1},
    {"task":"transpose", "semitones":13},
    {"task":"tune", "track_id":"a"*32, "strength":math.nan},
    {"task":"erase", "track_id":"a"*32, "start":1, "end":1},
    {"task":"tune", "track_id":"a"*32, "notes":[{"start":0,"end":1,"midi":60},{"start":.5,"end":2,"midi":62}]},
])
def test_reject_unsafe_job_options(payload):
    with pytest.raises(ValidationError):
        JobRequest(revision=1, **payload)


def test_automation_strictly_ordered():
    with pytest.raises(ValidationError):
        TrackPatch(revision=1, automation=[{"time":1,"db":0},{"time":1,"db":2}])
    patch = TrackPatch(revision=1, gain_db=0, pan=0, muted=False)
    assert patch.model_dump(exclude_none=True)["muted"] is False


def test_history_revision_and_branching(tmp_path):
    s = Store(tmp_path)
    p = s.create("one")
    p = s.mutate(p["id"], p["revision"], lambda p:p.update(name="two"))
    assert p["can_undo"] and p["name"] == "two"
    with pytest.raises(Conflict):
        s.mutate(p["id"], 1, lambda p:p.update(name="stale"))
    p = s.travel(p["id"], p["revision"], -1)
    assert p["name"] == "one" and p["revision"] == 3 and p["can_redo"]
    p = s.travel(p["id"], p["revision"], 1)
    assert p["name"] == "two" and p["revision"] == 4
    p = s.travel(p["id"], p["revision"], -1)
    p = s.mutate(p["id"], p["revision"], lambda p:p.update(name="branch"))
    assert not p["can_redo"]
    assert Store(tmp_path).get(p["id"])["name"] == "branch"


def test_transaction_rolls_back_on_failure(tmp_path):
    s=Store(tmp_path); p=s.create("intact")
    def invalid(doc):
        doc["name"]="changed"
        raise ValueError("abort")
    with pytest.raises(ValueError):
        s.mutate(p["id"], 1, invalid)
    assert s.get(p["id"])["name"] == "intact"


def test_active_job_locks_mutation_and_undo(tmp_path):
    s=Store(tmp_path);p=s.create("locked")
    job={"id":uid(),"project":p["id"],"state":"running"}
    s.save_job(job)
    with pytest.raises(Conflict): s.mutate(p["id"],1,lambda p:p.update(name="no"))
    with pytest.raises(Conflict): s.travel(p["id"],1,-1)
    p=s.mutate(p["id"],1,lambda p:p.update(name="worker"),from_job=True)
    assert p["name"] == "worker"


def test_assets_are_scoped_and_paths_cannot_escape(tmp_path):
    s=Store(tmp_path); p=s.create("A"); q=s.create("B"); a=uid()
    s.add_asset(p["id"],{"id":a,"filename":"../../outside.wav"})
    with pytest.raises(Missing): s.asset(q["id"],a)
    with pytest.raises(Missing): s.asset_path(p["id"],a)
    with pytest.raises(Missing): s.project_dir("../../etc")
    with pytest.raises(Missing): s.get("' OR 1=1--")
