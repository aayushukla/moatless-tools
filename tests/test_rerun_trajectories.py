import json

import pytest

from moatless.benchmark.swebench import load_instance, create_workspace
from moatless.benchmark.utils import get_file_spans_from_patch
from moatless.edit.edit import EditCode
from moatless.loop import AgenticLoop
from moatless.state import AgenticState
from moatless.transitions import code_transitions


def read_trajectory(path):
    with open(path, "r") as f:
        return json.load(f)


def get_actions(trajectory: dict):
    actions = []
    for transition in trajectory["transitions"]:
        for action in transition["actions"]:
            actions.append(action["action"])
    return actions


@pytest.mark.skip
def test_two_edits():
    path = "trajectories/two_edits.json"
    trajectory = read_trajectory(path)
    actions = get_actions(trajectory)

    instance = load_instance(trajectory["info"]["instance_id"])
    workspace = create_workspace(instance, repo_base_dir="/tmp/repos", index_store_dir="/home/albert/20240522-voyage-code-2")

    spans = get_file_spans_from_patch(workspace.file_repo, instance["patch"])
    for file_path, span_ids in spans.items():
        workspace.file_context.add_spans_to_context(
            file_path=file_path, span_ids=span_ids
        )

    iteration = 0

    def _verify_state_func(state: AgenticState):
        nonlocal iteration
        iteration += 1
        if isinstance(state, EditCode):
            assert state.file_path == "src/_pytest/mark/evaluate.py"
            if iteration == 2:
                # Remove function
                assert state.span_id == "cached_eval"
                assert state.start_line == 21
                assert state.end_line == 31
            elif iteration == 4:
                # Update the other function and expect lines to have been updated
                assert state.span_id == "MarkEvaluator._istrue"
                assert state.start_line == 71
                assert state.end_line == 110

    loop = AgenticLoop(
        code_transitions(),
        workspace=workspace,
        mocked_actions=actions,
        verify_state_func=_verify_state_func,
    )
    loop.run(message=trajectory["initial_message"])
