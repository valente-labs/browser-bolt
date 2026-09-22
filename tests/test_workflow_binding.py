"""Execute each workflow's actual pre-checkout binding script without network access."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
WORKFLOWS = ["public-canary.yml", "public-journey.yml"]
VALUES = {"REPOSITORY": "example/browser-bolt", "URL": "https://bolt.example.com", "RELEASE_ID": "a" * 64,
          "RELEASE_TAG": "v0.1.0", "MANIFEST_SHA": "b" * 64, "EXPECTED_SHA": "c" * 40}


def binding_script(name):
    workflow = (ROOT / ".github/workflows" / name).read_text()
    assert workflow.index("name: Bind reviewed monitoring identity") < workflow.index("uses: actions/checkout@")
    block = workflow.split("          python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return workflow, textwrap.dedent(block)


def bind(tmp_path, name, event, *, inputs=None, variables=None, github_sha=None):
    _, script = binding_script(name)
    destination = tmp_path / "github-env"
    inputs = VALUES if inputs is None else inputs
    variables = VALUES if variables is None else variables
    env = {"PATH": os.defpath, "GITHUB_ENV": str(destination), "GITHUB_EVENT_NAME": event,
           "GITHUB_REPOSITORY": VALUES["REPOSITORY"], "GITHUB_SHA": github_sha or VALUES["EXPECTED_SHA"],
           **{"INPUT_" + key: value for key, value in inputs.items()},
           **{"VAR_" + key: value for key, value in variables.items()}}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=5)
    values = dict(line.split("=", 1) for line in destination.read_text().splitlines()) if destination.exists() else {}
    return result, values


@pytest.mark.parametrize("name", WORKFLOWS)
@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule"])
def test_manual_inputs_and_schedule_variables_are_distinct(tmp_path, name, event):
    wrong = {key: "sentinel-secret" for key in VALUES}
    kwargs = {"variables": wrong} if event == "workflow_dispatch" else {"inputs": wrong}
    result, bound = bind(tmp_path, name, event, **kwargs)
    assert result.returncode == 0
    assert bound == {"BOLT_CANARY_REPOSITORY": VALUES["REPOSITORY"], "BOLT_CANARY_URL": VALUES["URL"],
                     "BOLT_CANARY_RELEASE": VALUES["RELEASE_ID"], "BOLT_CANARY_TAG": VALUES["RELEASE_TAG"],
                     "BOLT_CANARY_MANIFEST_SHA": VALUES["MANIFEST_SHA"], "BOLT_CHECKOUT_SHA": VALUES["EXPECTED_SHA"]}
    assert b"sentinel" not in result.stdout + result.stderr


@pytest.mark.parametrize("name", WORKFLOWS)
@pytest.mark.parametrize("missing", VALUES)
def test_missing_manual_input_fails_without_variable_fallback(tmp_path, name, missing):
    result, bound = bind(tmp_path, name, "workflow_dispatch", inputs={**VALUES, missing: ""})
    assert result.returncode != 0 and not bound


@pytest.mark.parametrize("name", WORKFLOWS)
@pytest.mark.parametrize("field,value", [
    ("EXPECTED_SHA", "d" * 40), ("REPOSITORY", "other/fork"),
    ("URL", "https://bolt.example.com\nsentinel-secret=value"), ("URL", "https://secret@bolt.example.com"),
    ("RELEASE_TAG", "latest"), ("MANIFEST_SHA", "sentinel-secret"),
])
def test_invalid_manual_identity_fails_before_checkout(tmp_path, name, field, value):
    result, bound = bind(tmp_path, name, "workflow_dispatch", inputs={**VALUES, field: value})
    assert result.returncode != 0 and not bound
    assert b"sentinel" not in result.stdout + result.stderr


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_uses_bound_checkout_and_never_reloads_identity_after_binding(name):
    workflow, _ = binding_script(name)
    checkout = workflow.split("      - uses: actions/checkout@", 1)[1]
    assert "ref: ${{ env.BOLT_CHECKOUT_SHA }}" in checkout
    assert "vars.BOLT_CANARY_" not in checkout
    for input_name in ("repository", "url", "release_id", "release_tag", "manifest_sha", "expected_sha"):
        assert f"      {input_name}:\n" in workflow
        assert f"${{{{ inputs.{input_name} }}}}" in workflow
    if name == "public-canary.yml":
        assert "run-name: Browser Bolt canary ${{ inputs.launch_id || 'scheduled' }}" in workflow
        assert "  probe:\n" in workflow and "name: Check the verified public release" in workflow
    else:
        assert "run-name: Browser Bolt journey ${{ inputs.launch_id || 'scheduled' }}" in workflow
        assert "  journey:\n" in workflow and "name: Verify rendered public pages and clean installation" in workflow
