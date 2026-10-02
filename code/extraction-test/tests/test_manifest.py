"""manifest.yaml must validate against the meetily-workflows catalog schema.

tests/fixtures/manifest.schema.json is copied from Zackriya-Solutions/meetily-workflows
(community-workflows/manifest.schema.json, MIT License).
"""
import json
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
MANIFEST = yaml.safe_load((HERE.parent / "manifest.yaml").read_text("utf-8"))
SCHEMA = json.loads((HERE / "fixtures" / "manifest.schema.json").read_text("utf-8"))


def test_manifest_matches_catalog_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(MANIFEST, SCHEMA)


def test_manifest_matches_the_code():
    assert (HERE.parents[2] / MANIFEST["entry"]).is_file()          # entry path is real
    assert set(MANIFEST["scopes"]) == {"read", "write"}             # least privilege: no record/delete
    assert MANIFEST["trigger"] == "recording-ends"          # we write on recording.stopped
