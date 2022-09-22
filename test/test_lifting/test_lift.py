from unittest import mock
from unittest.mock import patch, MagicMock, mock_open, call
from subprocess import CalledProcessError
import subprocess
import sys

import pytest

from binrec import lift, core
from binrec.lift import OptimizationLevel
from binrec.env import BINREC_ROOT, llvm_command
from binrec.errors import BinRecError
from binrec import audit

from helpers.mock_path import MockPath


READELF_DYNSYM_OUTPUT = """
Symbol table '.dynsym' contains 9 entries:
   Num:    Value  Size Type    Bind   Vis      Ndx Name
     0: 00000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 00000000     0 FUNC    GLOBAL DEFAULT  UND printf@GLIBC_2.0 (2)
     2: 00000000     0 FUNC    GLOBAL DEFAULT  UND fwrite@GLIBC_2.0 (2)
     3: 00000000     0 NOTYPE  WEAK   DEFAULT  UND __gmon_start__
     4: 00000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main@GLIBC_2.0 (2)
     5: 0804c044     4 OBJECT  GLOBAL DEFAULT   26 stdout@GLIBC_2.0 (2)
     6: 0804c020     4 OBJECT  GLOBAL DEFAULT   26 stderr@GLIBC_2.0 (2)
     7: 0804a004     4 OBJECT  GLOBAL DEFAULT   17 _IO_stdin_used
"""

READELF_SECTIONS_OUTPUT = """
There are 36 section headers, starting at offset 0x39e8:

Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 0]                   NULL            00000000 000000 000000 00      0   0  0
  [ 1] .interp           PROGBITS        080481b4 0001b4 000013 00   A  0   0  1
  [ 2] .note.gnu.build-i NOTE            080481c8 0001c8 000024 00   A  0   0  4
  [10] .rel              REL             0804830c 00030c 000008 08   A  6   0  4
Key to Flags:
  W (write), A (alloc), X (execute), M (merge), S (strings), I (info),
  L (link order), O (extra OS processing required), G (group), T (TLS),
  C (compressed), x (unknown), o (OS specific), E (exclude),
  p (processor specific)
"""


OBJDUMP_SYM_OUTPUT = b"""
abc12340 <foo@plt>:
    retl 0
"""

OBJDUMP_DEP_OUTPUT = b"""
not matched
  NEEDED      libselinux.so.1
other
  NEEDED      libc.so.6
  NEEDED      libnotfound.so.1
other
"""

LDD_DEP_OUTPUT = b"""
	linux-gate.so.1 (0xf7f10000)
	libselinux.so.1 => /lib/i386-linux-gnu/libselinux.so.1 (0xf7ec3000)
	libc.so.6 => /lib/i386-linux-gnu/libc.so.6 (0xf7cd4000)
	libpcre2-8.so.0 => /lib/i386-linux-gnu/libpcre2-8.so.0 (0xf7c3d000)
	libdl.so.2 => /lib/i386-linux-gnu/libdl.so.2 (0xf7c37000)
	/lib/ld-linux.so.2 (0xf7f12000)
	libpthread.so.0 => /lib/i386-linux-gnu/libpthread.so.0 (0xf7c14000)
"""

class TestLifting:

    @patch("builtins.open", new_callable=mock.mock_open)
    @patch.object(lift.subprocess, 'run')
    def test_extract_symbols(self, mock_run, mock_symbols):
        trace_dir = MagicMock(name="asdf")

        symbols_path = trace_dir / "symbols"

        mock_run.return_value.stdout = OBJDUMP_SYM_OUTPUT

        lift._extract_binary_symbols(trace_dir)

        mock_symbols.assert_called_once_with(symbols_path, "w")
        mock_run.assert_called_once_with(["objdump", "-d", "binary"],
            cwd=str(trace_dir), stdout=subprocess.PIPE)

    @patch.object(lift.subprocess, 'run')
    @patch.object(lift.subprocess, 'Popen')
    def test_extract_symbols_error(self, mock_popen, mock_run):
        mock_run.side_effect = CalledProcessError(0, 'asdf')
        with pytest.raises(BinRecError):
            lift._extract_binary_symbols(MagicMock(name="asdf"))

    @patch.object(lift.subprocess, "check_output")
    @patch.object(lift, "open", new_callable=mock_open)
    def test_extract_data_imports(self, mock_file, mock_check_output):
        trace_dir = MagicMock(name="asdf")
        mock_check_output.return_value = READELF_DYNSYM_OUTPUT.encode()
        lift._extract_data_imports(trace_dir)

        mock_check_output.assert_called_once_with(
            ["readelf", "--dyn-sym", str(trace_dir / "binary")]
        )
        mock_file.assert_called_once_with(trace_dir / "data_imports", "w")
        handle = mock_file()
        assert handle.write.call_args_list == [
            call("0804c044 4 stdout"),
            call("\n"),
            call("0804c020 4 stderr"),
            call("\n"),
        ]

    @patch.object(lift.subprocess, "check_output")
    def test_extract_data_imports_error(self, mock_check_output):
        mock_check_output.side_effect = CalledProcessError(0, "asdf")
        with pytest.raises(BinRecError):
            lift._extract_data_imports(MagicMock(name="asdf"))

    @patch.object(lift.subprocess, "check_output")
    @patch.object(lift, "open", new_callable=mock_open)
    def test_extract_sections(self, mock_file, mock_check_output):
        trace_dir = MagicMock(name="asdf")
        mock_check_output.return_value = READELF_SECTIONS_OUTPUT.encode()

        lift._extract_sections(trace_dir)

        mock_check_output.assert_called_once_with(
            ["readelf", "--section-headers", str(trace_dir / "binary")]
        )

        mock_file.assert_called_once_with(trace_dir / "sections", "w")
        handle = mock_file()
        assert handle.write.call_args_list == [
            call("080481b4 000013 .interp"),
            call("\n"),
            call("080481c8 000024 .note.gnu.build-i"),
            call("\n"),
            call("0804830c 000008 .rel"),
            call("\n")
        ]

    @patch.object(lift.subprocess, "check_output")
    def test_extract_sections_error(self, mock_check_output):
        mock_check_output.side_effect = CalledProcessError(0, "asdf")
        with pytest.raises(BinRecError):
            lift._extract_sections(MagicMock(name="asdf"))

    def test_clean_bitcode(self, mock_lib_module):
        trace_dir = MockPath("asdf")

        lift._clean_bitcode(trace_dir)

        mock_lib_module.binrec_lift.clean.assert_called_once_with(
            trace_filename="captured.bc",
            destination="cleaned",
            working_dir=str(trace_dir),
        )

    def test_clean_bitcode_error(self, mock_lib_module):
        mock_lib_module.binrec_lift.clean.side_effect = OSError()
        mock_lib_module.convert_lib_error.return_value = BinRecError('asdf')
        with pytest.raises(BinRecError):
            lift._clean_bitcode(MockPath("asdf"))

        mock_lib_module.convert_lib_error.assert_called_once()

    @patch.object(lift.subprocess, "check_call")
    def test_apply_fixups(self, mock_check_call):
        trace_dir = MockPath("asdf")

        lift._apply_fixups(trace_dir)

        assert mock_check_call.call_args_list == [
            call(
                [
                    llvm_command("llvm-link"),
                    "-o",
                    "linked.bc",
                    "cleaned.bc",
                    str(BINREC_ROOT / "runlib" / "custom-helpers.bc"),
                ],
                cwd=str(trace_dir),
                stdout=(trace_dir / "fixups.log").open.return_value,
                stderr=subprocess.STDOUT
            ),
            call(
                [llvm_command("llvm-dis"), "linked.bc"],
                cwd=str(trace_dir),
                stdout=(trace_dir / "fixups.log").open.return_value,
                stderr=subprocess.STDOUT
            ),
        ]

    @patch.object(lift.subprocess, "check_call")
    def test_apply_fixups_error(self, mock_check_call):
        mock_check_call.side_effect = CalledProcessError(0, "asdf")
        with pytest.raises(BinRecError):
            lift._apply_fixups(MockPath("asdf"))

    def test_lift_bitcode(self, mock_lib_module):
        trace_dir = MockPath("asdf")

        lift._lift_bitcode(trace_dir)

        mock_lib_module.binrec_lift.lift.assert_called_once_with(
            trace_filename="linked.bc",
            destination="lifted",
            clean_names=True,
            working_dir=str(trace_dir),
        )

    def test_lift_bitcode_error(self, mock_lib_module):
        mock_lib_module.binrec_lift.lift.side_effect = OSError()
        mock_lib_module.convert_lib_error.return_value = BinRecError('asdf')
        with pytest.raises(BinRecError):
            lift._lift_bitcode(MockPath("asdf"))

        mock_lib_module.convert_lib_error.assert_called_once()

    def test_optimize_bitcode(self, mock_lib_module):
        trace_dir = MockPath("asdf")

        lift._optimize_bitcode(trace_dir, lift.OptimizationLevel.NORMAL)

        mock_lib_module.binrec_lift.optimize(
            trace_filename="lifted.bc",
            destination="optimized",
            memssa_check_limit=100000,
            working_dir=str(trace_dir),
        )

    def test_optimize_bitcode_error(self, mock_lib_module):
        mock_lib_module.binrec_lift.optimize.side_effect = OSError()
        mock_lib_module.convert_lib_error.return_value = BinRecError('asdf')
        with pytest.raises(BinRecError):
            lift._optimize_bitcode(MockPath("asdf"), lift.OptimizationLevel.NORMAL)

        mock_lib_module.convert_lib_error.assert_called_once()

    @patch.object(lift.subprocess, "check_call")
    def test_disassemble_bitcode(self, mock_check_call):
        trace_dir = MockPath("asdf")

        lift._disassemble_bitcode(trace_dir)

        mock_check_call.assert_called_once_with(
            [llvm_command("llvm-dis"), "optimized.bc"],
            cwd=str(trace_dir),
            stdout=(trace_dir / "disassembly.log").open.return_value,
            stderr=subprocess.STDOUT
        )

    @patch.object(lift.subprocess, "check_call")
    def test_disassemble_bitcode_error(self, mock_check_call):
        mock_check_call.side_effect = CalledProcessError(0, "asdf")
        with pytest.raises(BinRecError):
            lift._disassemble_bitcode(MockPath("asdf"))

    def test_recover_bitcode(self, mock_lib_module):
        trace_dir = MockPath("asdf")

        lift._recover_bitcode(trace_dir)

        mock_lib_module.binrec_lift.compile_prep.assert_called_once_with(
            trace_filename="optimized.bc",
            destination="recovered",
            working_dir=str(trace_dir),
        )

    def test_recover_bitcode_error(self, mock_lib_module):
        mock_lib_module.binrec_lift.compile_prep.side_effect = OSError()
        mock_lib_module.convert_lib_error.return_value = BinRecError('asdf')
        with pytest.raises(BinRecError):
            lift._recover_bitcode(MockPath("asdf"))

        mock_lib_module.convert_lib_error.assert_called_once()

    @patch.object(lift.subprocess, "check_call")
    def test_compile_bitcode(self, mock_check_call):
        trace_dir = MockPath("asdf")
        lift._compile_bitcode(trace_dir)

        mock_check_call.assert_called_once_with(
            [
                llvm_command("llc"), "-filetype", "obj", "-o", "recovered.o",
                "recovered.bc"
            ],
            cwd=str(trace_dir),
            stdout=(trace_dir / "compile.log").open.return_value,
            stderr=subprocess.STDOUT
        )

    @patch.object(lift.subprocess, "check_call")
    def test_compile_bitcode_error(self, mock_check_call):
        mock_check_call.side_effect = CalledProcessError(0, "asdf")
        with pytest.raises(BinRecError):
            lift._compile_bitcode(MockPath("asdf"))

    def test_link_recovered_binary(self, mock_lib_module):
        trace_dir = MockPath("asdf")
        i386_ld = str(BINREC_ROOT / "binrec_link" / "ld" / "i386.ld")
        libbinrec_rt = str(BINREC_ROOT / "build" / "lib" / "libbinrec_rt.a")

        lift._link_recovered_binary(trace_dir)

        mock_lib_module.binrec_link.link.assert_called_once_with(
            binary_filename=str(trace_dir / "binary"),
            recovered_filename=str(trace_dir / "recovered.o"),
            runtime_library=libbinrec_rt,
            linker_script=i386_ld,
            destination=str(trace_dir / "recovered"),
            dependencies_filename=str(trace_dir / "dependencies"),
            harden=False
        )

    def test_link_recovered_binary_error(self, mock_lib_module):
        mock_lib_module.binrec_link.link.side_effect = CalledProcessError(0, "asdf")
        mock_lib_module.convert_lib_error.return_value = BinRecError('asdf')
        with pytest.raises(BinRecError):
            lift._link_recovered_binary(MagicMock(name="asdf"))

        mock_lib_module.convert_lib_error.assert_called_once()

    @patch.object(lift, "_extract_binary_symbols")
    @patch.object(lift, "_extract_data_imports")
    @patch.object(lift, "_extract_sections")
    @patch.object(lift, "_extract_dependencies")
    @patch.object(lift, "_clean_bitcode")
    @patch.object(lift, "_apply_fixups")
    @patch.object(lift, "_lift_bitcode")
    @patch.object(lift, "_optimize_bitcode")
    @patch.object(lift, "_disassemble_bitcode")
    @patch.object(lift, "_recover_bitcode")
    @patch.object(lift, "_compile_bitcode")
    @patch.object(lift, "_link_recovered_binary")
    @patch.object(lift, "project")
    def test_lift_trace(
        self,
        mock_project,
        mock_link,
        mock_compile,
        mock_recover,
        mock_disasm,
        mock_optimize,
        mock_lift,
        mock_apply,
        mock_clean,
        mock_deps,
        mock_sections,
        mock_data_imports,
        mock_extract,
    ):
        mock_project.merged_trace_dir.return_value = trace_dir = MockPath(
            "s2e-out", is_dir=True
        )
        lift.lift_trace("hello", OptimizationLevel.NORMAL)
        mock_extract.assert_called_once_with(trace_dir)
        mock_clean.assert_called_once_with(trace_dir)
        mock_apply.assert_called_once_with(trace_dir)
        mock_lift.assert_called_once_with(trace_dir)
        mock_optimize.assert_called_once_with(trace_dir, OptimizationLevel.NORMAL)
        mock_disasm.assert_called_once_with(trace_dir)
        mock_recover.assert_called_once_with(trace_dir)
        mock_compile.assert_called_once_with(trace_dir)
        mock_link.assert_called_once_with(trace_dir, False)
        mock_data_imports.assert_called_once_with(trace_dir)
        mock_sections.assert_called_once_with(trace_dir)
        mock_deps.assert_called_once_with(trace_dir)

    @patch.object(lift, "_extract_binary_symbols")
    @patch.object(lift, "_extract_sections")
    @patch.object(lift, "_extract_dependencies")
    @patch.object(lift, "_clean_bitcode")
    @patch.object(lift, "_apply_fixups")
    @patch.object(lift, "_lift_bitcode")
    @patch.object(lift, "_optimize_bitcode")
    @patch.object(lift, "_disassemble_bitcode")
    @patch.object(lift, "_recover_bitcode")
    @patch.object(lift, "_compile_bitcode")
    @patch.object(lift, "_link_recovered_binary")
    @patch.object(lift, "project")
    def test_lift_trace_error(
        self,
        mock_project,
        mock_link,
        mock_compile,
        mock_recover,
        mock_disasm,
        mock_optimize,
        mock_lift,
        mock_apply,
        mock_clean,
        mock_deps,
        mock_sections,
        mock_extract,
    ):
        mock_project.merged_trace_dir.return_value = MockPath("s2e-out", exists=False)
        with pytest.raises(BinRecError):
            lift.lift_trace("hello", OptimizationLevel.NORMAL)

        mock_extract.assert_not_called()
        mock_clean.assert_not_called()
        mock_apply.assert_not_called()
        mock_lift.assert_not_called()
        mock_optimize.assert_not_called()
        mock_disasm.assert_not_called()
        mock_recover.assert_not_called()
        mock_compile.assert_not_called()
        mock_link.assert_not_called()
        mock_sections.assert_not_called()
        mock_deps.assert_not_called()

    @patch("sys.argv", ["merge", "hello"])
    @patch.object(sys, "exit")
    @patch.object(lift, "lift_trace")
    def test_main(self, mock_lift, mock_exit):
        lift.main()
        mock_lift.assert_called_once_with("hello", OptimizationLevel.NORMAL, False)
        mock_exit.assert_called_once_with(0)

    @patch("sys.argv", ["lift"])
    def test_main_usage_error(self):
        with pytest.raises(SystemExit) as err:
            lift.main()

        assert err.value.code == 2

    @patch("sys.argv", ["lift", "-v", "hello"])
    @patch.object(sys, "exit")
    @patch.object(lift, "lift_trace")
    @patch.object(audit, "enable_python_audit_log")
    @patch.object(core, "enable_binrec_debug_mode")
    def test_main_verbose(self, mock_debug, mock_audit, mock_lift, mock_exit):
        lift.main()
        mock_audit.assert_not_called()
        mock_debug.assert_called_once()

    @patch("sys.argv", ["lift", "-vv", "hello"])
    @patch.object(sys, "exit")
    @patch.object(lift, "lift_trace")
    @patch.object(audit, "enable_python_audit_log")
    @patch.object(core, "enable_binrec_debug_mode")
    def test_main_audit(self, mock_debug, mock_audit, mock_lift, mock_exit):
        lift.main()
        mock_audit.assert_called_once()
        mock_debug.assert_called_once()

    @patch("builtins.open", new_callable=mock.mock_open)
    @patch.object(lift, "print")
    @patch.object(lift.subprocess, "check_output")
    def test_extract_dependencies(self, mock_check, mock_print, mock_file):
        trace_dir = MockPath(name="asdf")
        deps_path = trace_dir / "dependencies"
        binary = trace_dir / "binary"
        mock_check.side_effect = [OBJDUMP_DEP_OUTPUT, LDD_DEP_OUTPUT]

        lift._extract_dependencies(trace_dir)

        mock_file.assert_called_once_with(deps_path, "w")
        handle = mock_file()
        assert mock_print.call_args_list == [
            call("/lib/i386-linux-gnu/libselinux.so.1", file=handle),
	        call("/lib/i386-linux-gnu/libc.so.6", file=handle)
        ]
        assert mock_check.call_args_list == [
            call(["objdump", "--private-headers", str(binary)]),
            call(["ldd", str(binary)])
        ]

    @patch.object(lift.subprocess, "check_output")
    def test_extract_dependencies_objdump_err(self, mock_check):
        trace_dir = MockPath(name="asdf")
        mock_check.side_effect = [subprocess.CalledProcessError(-1, ""), b""]
        with pytest.raises(BinRecError):
            lift._extract_dependencies(trace_dir)

    @patch.object(lift.subprocess, "check_output")
    def test_extract_dependencies_ldd_err(self, mock_check):
        trace_dir = MockPath(name="asdf")
        mock_check.side_effect = [OBJDUMP_DEP_OUTPUT, subprocess.CalledProcessError(-1, "")]
        with pytest.raises(BinRecError):
            lift._extract_dependencies(trace_dir)
