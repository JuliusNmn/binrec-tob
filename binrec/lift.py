import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

from . import project
from .env import BINREC_LIB, BINREC_LINK_LD, BINREC_RUNLIB, BINREC_SCRIPTS, llvm_command
from .errors import BinRecError
from .lib import binrec_lift, binrec_link

logger = logging.getLogger("binrec.lift")

# This is related to issue #39 and is commented out for now.
# SOFTFLOAT_BC = (BINREC_ROOT / "s2e" / "build" / "tools-release"/ "lib" /
#                 "X86BitcodeLibrary" /"softfloat.bc")


def prep_bitcode_for_linkage(
    working_dir: Path, source: Path, destination: Path
) -> None:
    """
    Prepare captured bitcode for linkage. This was originally the content of the
    ``prep_for_linkage.sh`` script.

    :param working_dir: S2E capture directory (typically ``s2e-out-*``)
    :param source: the input bitcode filename
    :param destination: the output bitcode filename
    """
    logger.debug("preparing capture bitcode for linkage: %s", source)

    fd, tmp = tempfile.mkstemp()
    os.close(fd)

    # tmp.bc is created by the lifting scripts
    tmp_bc = Path(f"{tmp}.bc")

    cwd = str(working_dir)

    if not source.is_absolute():
        source = working_dir / source
    src = str(source)

    if not destination.is_absolute():
        destination = working_dir / destination
    dest = str(destination)

    try:
        binrec_lift.link_prep_1(trace_filename=src, destination=tmp, working_dir=cwd)
    except Exception as err:
        os.remove(tmp)
        with suppress(OSError):
            os.remove(tmp_bc)
        raise BinRecError(
            f"failed first stage of linkage prep for bitcode: {source}: {err}"
        )

    # TODO: This is commented out due to open issue #39.
    # The code for this issue is commented out and binrec, with the updated s2e,
    # appears to be working. I'm leaving in the commented out code block for now since
    # I'm not absolutely confident that we are exercising floating point math fully in
    # our current integration test samples.
    # try:
    #     # softfloat appears to be part of qemu and implements floating point math
    #     subprocess.check_call(
    #         [
    #             "llvm-link-12",
    #             "-o",
    #             dest,
    #             "--override",
    #             tmp_bc,
    #             # str(SOFTFLOAT_BC),
    #             str(BINREC_RUNLIB / "softfloat.bc"),
    #         ],
    #         cwd=cwd,
    #     )
    # except subprocess.CalledProcessError:
    #     # The original script ran this in via "eval", which does not honor "set -e".
    #     # So, I'm seeing this llvm-link call fail consistently when run against a
    #     # merged capture (s2e-out-binary-1/merged), but the script continues and
    #     # properly finishes the merging / lifting.
    #     #
    #     # If this call fails then the destination file won't exist and the next call
    #     # to binrec_lift won't be run.
    #     #
    #     # TODO add a check that determine whether llvm-link is needed before calling
    #     # it.
    #     logger.warning(
    #         "failed llvm link prep for bitcode (this may be normal if the "
    #         "bitcode has already been merged from multiple captures): %s",
    #         src,
    #     )

    shutil.move(tmp_bc, destination)
    try:
        binrec_lift.link_prep_2(trace_filename=dest, destination=tmp, working_dir=cwd)
    except Exception as err:
        with suppress(OSError):
            os.remove(tmp_bc)

        os.remove(tmp)
        raise BinRecError(
            f"failed second stage of linkage prep for bitcode: {source}: {err}"
        )

    shutil.move(tmp_bc, destination)
    os.remove(tmp)


def _extract_binary_symbols(trace_dir: Path) -> None:
    """
    Extract the symbols from the original binary.

    **Inputs:** trace_dir / "binary"

    **Outputs:** trace_dir / "symbols"

    :param trace_dir: binrec binary trace directory
    """
    makefile = str(BINREC_SCRIPTS / "s2eout_makefile")
    logger.debug("extracting symbols from binary: %s", trace_dir.name)
    try:
        subprocess.check_call(["make", "-f", makefile, "symbols"], cwd=str(trace_dir))
    except subprocess.CalledProcessError:
        raise BinRecError(f"failed to extract symbols from trace: {trace_dir.name}")


def _clean_bitcode(trace_dir: Path) -> None:
    """
    Clean the trace for linking with custom helpers.

    **Inputs:** trace_dir / captured.bc

    **Outputs:**

    - trace_dir / "cleaned.bc"
    - trace_dir / "cleaned.ll"
    - trace_dir / "cleaned-memssa.ll"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug("cleaning captured bitcode: %s", trace_dir.name)
    try:
        binrec_lift.clean(
            trace_filename="captured.bc",
            destination="cleaned",
            working_dir=str(trace_dir),
        )
    except Exception as err:
        raise BinRecError(
            f"failed to clean captured LLVM bitcode: {trace_dir.name}: {err}"
        )


def _apply_fixups(trace_dir: Path) -> None:
    """
    Apply binrec fixups to the cleaned bitcode.

    **Inputs:** trace_dir / "cleaned.bc"

    **Outputs:** trace_dir / "linked.bc"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug("applying fixups to captured bitcode: %s", trace_dir.name)
    try:
        subprocess.check_call(
            [
                llvm_command("llvm-link"),
                "-o",
                "linked.bc",
                "cleaned.bc",
                str(BINREC_RUNLIB / "custom-helpers.bc"),
            ],
            cwd=str(trace_dir),
        )
    except subprocess.CalledProcessError:
        raise BinRecError(
            f"failed to apply fixups to captured LLVM bitcode: {trace_dir.name}"
        )


def _lift_bitcode(trace_dir: Path) -> None:
    """
    Lift trace into correct LLVM module.

    **Inputs:** trace_dir / "linked.bc"

    **Outputs:**
      - trace_dir / "lifted.bc"
      - trace_dir / "lifted.ll"
      - trace_dir / "lifted-memssa.ll"
      - trace_dir / "rfuncs"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug(
        "performing initial lifting of captured LLVM bitcode: %s", trace_dir.name
    )
    try:
        binrec_lift.lift(
            trace_filename="linked.bc",
            destination="lifted",
            working_dir=str(trace_dir),
            clean_names=True,
        )
    except Exception as err:
        raise BinRecError(
            f"failed to perform initial lifting of LLVM bitcode: {trace_dir.name}: "
            f"{err}"
        )


def _optimize_bitcode(trace_dir: Path) -> None:
    """
    Optimize the lifted LLVM module.

    **Inputs:** trace_dir / "lifted.bc"

    **Outputs:**
        - trace_dir / "optimized.bc"
        - trace_dir / "optimized.ll"
        - trace_dir / "optimized-memssa.ll"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug("optimizing lifted bitcode: %s", trace_dir.name)
    try:
        binrec_lift.optimize(
            trace_filename="lifted.bc",
            destination="optimized",
            memssa_check_limit=100000,
            working_dir=str(trace_dir),
        )
    except Exception as err:
        raise BinRecError(
            f"failed to optimized lifted LLVM bitcode: {trace_dir.name}: {err}"
        )


def _disassemble_bitcode(trace_dir: Path) -> None:
    """
    Disassemble optimized bitcode.

    **Inputs:** trace_dir / "optimized.bc"

    **Outputs:** trace_dir / "optimized.ll"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug("disassembling optimized bitcode: %s", trace_dir.name)
    try:
        subprocess.check_call(
            [llvm_command("llvm-dis"), "optimized.bc"], cwd=str(trace_dir)
        )
    except subprocess.CalledProcessError:
        raise BinRecError(
            f"failed to disassemble captured LLVM bitcode: {trace_dir.name}"
        )


def _recover_bitcode(trace_dir: Path) -> None:
    """
    Compile the trace to an object file. This method recovers the optimized bitcode.

    **Inputs:** trace_dir / "optimized.bc"

    **Outputs:**
        - trace_dir / "recovered.bc"
        - trace_dir / "recovered.ll"
        - trace_dir / "recovered-memssa.ll"

    :param trace_dir: binrec binary trace directory
    """
    logger.debug("lowering optimized LLVM bitcode for compilation: %s", trace_dir.name)
    try:
        binrec_lift.compile_prep(
            trace_filename="optimized.bc",
            destination="recovered",
            working_dir=str(trace_dir),
        )
    except Exception as err:
        raise BinRecError(
            f"failed to lower optimized LLVM bitcode: {trace_dir.name}: {err}"
        )


def _compile_bitcode(trace_dir: Path) -> None:
    """
    Compile the recovered bitcode.

    **Inputs:** trace_dir / "recovered.bc"

    **Outputs:** trace_dir / "recovered.o"

    :param trace_dir: binrec binary trace directory
    """
    makefile = str(BINREC_SCRIPTS / "s2eout_makefile")
    logger.debug("compiling recovered LLVM bitcode: %s", trace_dir.name)
    try:
        subprocess.check_call(
            ["make", "-f", makefile, "-sr", "recovered.o"], cwd=str(trace_dir)
        )
    except subprocess.CalledProcessError:
        raise BinRecError(f"failed to compile recovered LLVM bitcode: {trace_dir.name}")


def _link_recovered_binary(trace_dir: Path) -> None:
    """
    Linked the recovered binary.

    **Inputs:** trace_dir / "recovered.o"

    **Outputs:** trace_dir / "recovered"

    :param trace_dir: binrec binary trace directory
    """
    i386_ld = str(BINREC_LINK_LD / "i386.ld")
    libbinrec_rt = str(BINREC_LIB / "libbinrec_rt.a")

    logger.debug("linking recovered binary: %s", trace_dir.name)
    try:
        binrec_link.link(
            binary_filename=str(trace_dir / "binary"),
            recovered_filename=str(trace_dir / "recovered.o"),
            runtime_library=libbinrec_rt,
            linker_script=i386_ld,
            destination=str(trace_dir / "recovered"),
        )
    except Exception as err:
        raise BinRecError("failed to link recovered binary: %s", err)


def lift_trace(project_name: str) -> None:
    """
    Lift and recover a binary from a binrec trace. This lifts, compiles, and links
    capture bitcode to a recovered binary. This method works on the binrec trace
    directory, ``s2e-out-<binary>``.

    :param binary: binary name
    """
    merged_trace_dir = project.merged_trace_dir(project_name)
    if not merged_trace_dir.is_dir():
        raise BinRecError(
            f"nothing to lift: directory does not exist: {merged_trace_dir}"
        )

    # Step 1: extract symbols
    _extract_binary_symbols(merged_trace_dir)

    # Step 2: clean captured LLVM bitcode
    _clean_bitcode(merged_trace_dir)

    # Step 3: apply fixups to captured bitcode
    _apply_fixups(merged_trace_dir)

    # Step 4: initial lifting
    _lift_bitcode(merged_trace_dir)

    # Step 5: optimize the lifted bitcode
    _optimize_bitcode(merged_trace_dir)

    # Step 6: disassemble optimized bitcode
    _disassemble_bitcode(merged_trace_dir)

    # Step 7: recover the optimized bitcode
    _recover_bitcode(merged_trace_dir)

    # Step 8: compile recovered bitcode
    _compile_bitcode(merged_trace_dir)

    # Step 9: Link the recovered binary
    _link_recovered_binary(merged_trace_dir)

    logger.info(
        "successfully lifted and recovered binary for project %s: %s",
        project_name,
        merged_trace_dir / "recovered",
    )


def main() -> None:
    import argparse
    import sys

    from .core import init_binrec

    init_binrec()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="count", help="enable verbose logging"
    )
    parser.add_argument("project_name", help="lift and compile the binary trace")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger("binrec").setLevel(logging.DEBUG)
        if args.verbose > 1:
            from .audit import enable_python_audit_log

            enable_python_audit_log()

    lift_trace(args.project_name)
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
