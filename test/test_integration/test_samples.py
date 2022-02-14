import os
import subprocess
import json
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import MagicMock

from _pytest.python import Metafunc

from binrec.env import BINREC_ROOT, BINREC_SCRIPTS
from binrec.merge import merge_traces
from binrec import lift, project

QEMU_DIR = BINREC_ROOT / "qemu"
TEST_INPUT_DIR = BINREC_ROOT / "test" / "benchmark" / "samples" / "binrec" / "test_inputs"
TEST_BUILD_DIR = BINREC_ROOT / "test" / "benchmark" / "samples" / "bin" / "x86" / "binrec"


@contextmanager
def load_real_lib():
    """
    Load the real C modules so they can be called during the integration tests.
    """
    import _binrec_link
    import _binrec_lift

    lift.binrec_link = _binrec_link
    lift.binrec_lift = _binrec_lift

    yield

    lift.binrec_link = MagicMock()
    lift.binrec_lift = MagicMock()


@dataclass
class TraceParams:
    args: List[str] = field(default_factory=list)
    stdin: str = ''

    @classmethod
    def load_dict(cls, item: dict) -> "TraceParams":
        args = item.get("args") or []
        stdin = item.get("stdin") or None

        return cls(args=args, stdin=stdin)


@dataclass
class TraceTestPlan:
    binary: Path
    traces: List[TraceParams]

    @property
    def project(self) -> str:
        return self.binary.name

    @classmethod
    def load_plaintext(cls, binary: Path, filename: Path) -> 'TraceTestPlan':
        with open(filename, 'r') as file:
            # read command line invocations
            invocations = [line.strip().split() for line in file]
            if not invocations:
                # no command line arguments specified, run the test once with no arguments
                invocations = [[]]

        return TraceTestPlan(binary, [TraceParams(args) for args in invocations])

    @classmethod
    def load_json(cls, binary: Path, filename: Path) -> 'TraceTestPlan':
        with open(filename, 'r') as file:
            body = json.loads(file.read().strip())

        return TraceTestPlan(binary, [TraceParams.load_dict(item)
                                      for item in body["runs"]])


def load_sample_test_cases(func: callable) -> callable:
    """
    Load sample test cases from the test input directory.
    """
    test_plans: List[Tuple[Path, Path]] = []

    if not TEST_INPUT_DIR.is_dir() or not TEST_BUILD_DIR.is_dir():
        func.sample_names = []
        return func

    for filename in sorted(os.listdir(TEST_INPUT_DIR)):
        binary = TEST_BUILD_DIR / filename
        if binary.suffix.lower() == ".json":
            binary = binary.with_suffix('')

        if not binary.is_file():
            continue

        test_plans.append((binary, TEST_INPUT_DIR / filename))

    func.test_plans = test_plans
    return func


def pytest_generate_tests(metafunc: Metafunc):
    """
    This method is called by pytest to generate test cases for this module.
    """
    test_plans: Optional[List[Tuple[Path, Path]]] = getattr(metafunc.function, "test_plans", None)
    if test_plans:
        metafunc.parametrize("binary,test_plan_file", test_plans, ids=[binary.name for binary, _ in test_plans])


@load_sample_test_cases
def test_sample(binary: Path, test_plan_file: Path):
    if test_plan_file.suffix.lower() == ".json":
        json_input = True
    else:
        json_input = False

    if json_input:
        plan = TraceTestPlan.load_json(binary, test_plan_file)
    else:
        plan = TraceTestPlan.load_plaintext(binary, test_plan_file)

    with load_real_lib():
        run_test(plan)


def assert_subprocess(*args, assert_error: str = "", **kwargs) -> None:
    """
    Run a subprocess and assert that is exits with a 0 return code.
    """
    proc = subprocess.run(*args, **kwargs)
    assert proc.returncode == 0, assert_error


def compare_lift(plan: TraceTestPlan) -> None:
    """
    Compare the original binary against the lifted binary for each test input. This method runs
    the original and the lifted and then compares the process return code, stdout, and stderr
    content. An ``AssertionError`` is raised if any of the comparison criteria does not match
    between the original and lifted sample.

    :param plan: test plan to execute and compare the original against the recovered
    """
    merged_dir = project.merged_trace_dir(plan.project)
    lifted = str(merged_dir / "recovered")
    original = str(merged_dir / "binary")
    target = str(merged_dir / "test-target")

    for trace in plan.traces:
        # We link to the binary we are running to make sure argv[0] is the same for the original
        # and the lifted program.
        os.link(original, target)
        print(">> running original sample with args:", trace.args)
        original_proc = subprocess.Popen(
            [target] + trace.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        # stdin is not supported yet in the updated s2e integration
        # if trace.stdin:
        #     original_proc.stdin.write(trace.stdin.encode())

        original_proc.stdin.close()
        original_proc.wait()
        os.remove(target)

        original_stdout = original_proc.stdout.read()
        original_stderr = original_proc.stderr.read()

        os.link(lifted, target)
        print(">> running recovered sample with args:", trace.args)
        lifted_proc = subprocess.Popen(
            [target] + trace.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        if trace.stdin:
            lifted_proc.stdin.write(trace.stdin.encode())

        lifted_proc.stdin.close()
        lifted_proc.wait()
        os.remove(target)

        lifted_stdout = lifted_proc.stdout.read()
        lifted_stderr = lifted_proc.stderr.read()

        assert original_proc.returncode == lifted_proc.returncode
        assert original_stdout == lifted_stdout
        assert original_stderr == lifted_stderr


def run_test(plan: TraceTestPlan) -> None:
    """
    Run a test binary, merge results, and lift the results. The binary may be executed
    multiple times depending on how many items are in the ``plan.traces`` parameter.

    :param binary: test binary name
    :param plan: trace test plan containing list of command line arguments and stdin
        input to use
    """
    project_dir = project.new_project(plan.project, plan.binary)

    print(">> running", len(plan.traces), "traces")
    for i, trace in enumerate(plan.traces, start=1):
        print(">> starting trace", i, "with arguments:", trace.args)
        if trace.stdin:
            print("warning: stdin is not supported yet")

        project.run_project(plan.project, trace.args)

    # Merge traces
    merge_traces(plan.project)

    # Lift
    lift.lift_trace(plan.project)

    print(
        ">> successfully ran and merged",
        len(plan.traces),
        "traces, verifying recovered binary",
    )
    compare_lift(plan)
    print(">> verified recovered binary")
