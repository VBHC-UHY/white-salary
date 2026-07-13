from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="POSIX bash execution runs in Linux CI"
)


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_python(
    path: Path,
    *,
    version: str,
    supported: bool = True,
    venv: bool = True,
    ensurepip: bool = True,
    pip: bool = True,
) -> None:
    major, minor, *_ = version.split(".")
    _write_executable(
        path,
        f"""
        #!/usr/bin/env bash
        if [[ "${{1:-}}" == "-c" ]]; then
          code="${{2:-}}"
          if [[ "$code" == *"platform.python_version"* ]]; then
            printf '%s\n' '{version}'
            exit 0
          fi
          if [[ "$code" == *"sys.version_info.major"* ]]; then
            printf '%s\n' '{major}.{minor}'
            exit 0
          fi
          exit {0 if supported else 1}
        fi
        if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" && "${{3:-}}" == "--help" ]]; then
          exit {0 if venv else 1}
        fi
        if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "ensurepip" ]]; then
          exit {0 if ensurepip else 1}
        fi
        if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pip" && "${{3:-}}" == "--version" ]]; then
          exit {0 if pip else 1}
        fi
        exit 1
        """,
    )


def _installer_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(PROJECT_ROOT / "install.sh", project / "install.sh")
    for name in ("pyproject.toml", "run_server.py", "conf.default.yaml"):
        (project / name).write_text("# test fixture\n", encoding="utf-8")
    return project


def _server_project(tmp_path: Path, *, first_run_exit: int = 0) -> Path:
    project = tmp_path / "server project"
    project.mkdir()
    shutil.copy2(PROJECT_ROOT / "server-setup.sh", project / "server-setup.sh")
    _write_executable(project / "install.sh", "#!/usr/bin/env bash\nexit 0\n")
    python_path = project / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.symlink_to(Path(sys.executable))
    (project / "conf.default.yaml").write_text(
        "llm:\n  api_key: ''\nserver:\n  host: 127.0.0.1\n  port: 12400\n",
        encoding="utf-8",
    )
    (project / "run_server.py").write_text("# fixture\n", encoding="utf-8")
    (project / "scripts").mkdir()
    (project / "scripts" / "first_run_check.py").write_text(
        f"raise SystemExit({first_run_exit})\n", encoding="utf-8"
    )
    return project


def _clean_env(bin_dir: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHON", None)
    env.pop("SUDO_USER", None)
    if bin_dir is not None:
        env["PATH"] = os.pathsep.join((str(bin_dir), "/usr/bin", "/bin"))
    return env


def _snapshot(root: Path) -> tuple[tuple[str, int, str], ...]:
    entries: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            payload = f"link:{os.readlink(path)}"
        elif path.is_file():
            payload = path.read_bytes().hex()
        else:
            payload = "directory"
        entries.append((relative, mode, payload))
    return tuple(entries)


class TestLinuxInstallerContracts:
    def test_installer_isolated_environment_contract(self) -> None:
        text = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        assert 'VENV_DIR="$PROJECT_ROOT/.venv"' in text
        assert '"$VENV_PYTHON" -m pip install' in text
        assert "python_can_seed_venv" in text
        assert "python_has_venv_module" in text
        assert "--install-system-deps" in text
        assert "--check" in text
        assert "uv venv --seed" in text
        assert "uv python find --no-python-downloads --no-cache" in text
        assert "venv_has_safe_identity" in text
        assert "pip install --user" not in text
        assert "sudo pip" not in text

    def test_server_wizard_security_and_service_contract(self) -> None:
        text = (PROJECT_ROOT / "server-setup.sh").read_text(encoding="utf-8")
        assert "./install.sh" in text
        assert "chmod 600 conf.yaml" in text
        assert "systemd_quote" in text
        assert "WorkingDirectory=$root_escaped" in text
        assert "ExecStart=$python_quoted $server_quoted" in text
        assert "determine_service_user" in text
        assert "Refusing to create a systemd service that runs as root" in text
        assert "repair_sudo_ownership" in text
        assert "systemd-analyze verify" in text
        assert 'if ! "${prefix[@]}" install' in text
        assert 'if ! "${prefix[@]}" systemctl daemon-reload' in text
        assert 'if ! "${prefix[@]}" systemctl enable --now' in text
        assert "first_run_check.py || true" not in text
        assert "PYTHONPATH=src .venv/bin/python run_server.py" in text
        assert "Ã¥Â®â€°Ã¨Â£" not in text


class TestLinuxInstallerBehavior:
    @POSIX_ONLY
    def test_bash_syntax_and_help(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash unavailable")
        for script in ("install.sh", "server-setup.sh"):
            path = PROJECT_ROOT / script
            subprocess.run([bash, "-n", str(path)], check=True)
            result = subprocess.run(
                [bash, str(path), "--help"],
                check=True,
                capture_output=True,
                text=True,
            )
            assert "Usage:" in result.stdout

    @POSIX_ONLY
    def test_invalid_server_port_fails_before_install(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash unavailable")
        result = subprocess.run(
            [bash, str(PROJECT_ROOT / "server-setup.sh"), "--port", "70000", "--yes"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "Port must be an integer" in result.stdout

    @POSIX_ONLY
    @pytest.mark.parametrize("broken_capability", ["ensurepip", "venv"])
    def test_python_selection_skips_higher_incomplete_candidate(
        self, tmp_path: Path, broken_capability: str
    ) -> None:
        project = _installer_project(tmp_path)
        bin_dir = tmp_path / "bin"
        _fake_python(
            bin_dir / "python3.12",
            version="3.12.7",
            ensurepip=broken_capability != "ensurepip",
            venv=broken_capability != "venv",
        )
        _fake_python(bin_dir / "python3.11", version="3.11.9")

        result = subprocess.run(
            ["bash", str(project / "install.sh"), "--check"],
            cwd=project,
            env=_clean_env(bin_dir),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Compatible Python 3.11.9 found (python3.11 on PATH)" in result.stdout

    @POSIX_ONLY
    def test_explicit_python_is_validated_even_with_valid_venv(self, tmp_path: Path) -> None:
        project = _installer_project(tmp_path)
        _fake_python(project / ".venv" / "bin" / "python", version="3.11.9")
        override = tmp_path / "python3.13"
        _fake_python(override, version="3.13.1", supported=False)

        result = subprocess.run(
            ["bash", str(project / "install.sh"), "--check", "--python", str(override)],
            cwd=project,
            env=_clean_env(),
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "configured Python is missing or unsupported" in result.stderr

    @POSIX_ONLY
    def test_check_is_read_only_and_uv_find_stops_after_first_suitable(
        self, tmp_path: Path
    ) -> None:
        project = _installer_project(tmp_path)
        bin_dir = tmp_path / "bin"
        uv_log = tmp_path / "uv.log"
        incomplete = tmp_path / "uv-python-3.12"
        complete = tmp_path / "uv-python-3.11"
        _fake_python(bin_dir / "python3.12", version="3.12.7", ensurepip=False)
        _fake_python(incomplete, version="3.12.7", ensurepip=False)
        _fake_python(complete, version="3.11.9")
        _write_executable(
            bin_dir / "uv",
            f"""
            #!/usr/bin/env bash
            printf '%s\n' "$*" >> {uv_log}
            if [[ "${{1:-}} ${{2:-}}" == "python find" ]]; then
              case "${{@: -1}}" in
                3.12) printf '%s\n' {incomplete} ;;
                3.11) printf '%s\n' {complete} ;;
                3.10) exit 91 ;;
              esac
              exit 0
            fi
            if [[ "${{1:-}} ${{2:-}}" == "venv --help" ]]; then
              exit 0
            fi
            exit 1
            """,
        )
        before = _snapshot(project)

        result = subprocess.run(
            ["bash", str(project / "install.sh"), "--check"],
            cwd=project,
            env=_clean_env(bin_dir),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _snapshot(project) == before
        calls = uv_log.read_text(encoding="utf-8").splitlines()
        find_calls = [line for line in calls if line.startswith("python find")]
        assert find_calls == [
            "python find --no-python-downloads --no-cache 3.12",
            "python find --no-python-downloads --no-cache 3.11",
        ]
        assert "3.10" not in "\n".join(find_calls)

    @POSIX_ONLY
    def test_refuses_to_delete_unidentified_nonempty_venv(self, tmp_path: Path) -> None:
        project = _installer_project(tmp_path)
        sentinel = project / ".venv" / "keep-me.txt"
        sentinel.parent.mkdir()
        sentinel.write_text("not a virtualenv\n", encoding="utf-8")
        candidate = tmp_path / "python3.11"
        _fake_python(candidate, version="3.11.9")

        result = subprocess.run(
            [
                "bash",
                str(project / "install.sh"),
                "--recreate-venv",
                "--python",
                str(candidate),
            ],
            cwd=project,
            env=_clean_env(),
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "identity could not be verified" in result.stdout
        assert sentinel.read_text(encoding="utf-8") == "not a virtualenv\n"

    @POSIX_ONLY
    def test_server_check_is_read_only(self, tmp_path: Path) -> None:
        project = tmp_path / "check server"
        project.mkdir()
        shutil.copy2(PROJECT_ROOT / "server-setup.sh", project / "server-setup.sh")
        _write_executable(project / "install.sh", "#!/usr/bin/env bash\nexit 0\n")
        before = _snapshot(project)

        result = subprocess.run(
            ["bash", str(project / "server-setup.sh"), "--check"],
            cwd=project,
            env=_clean_env(),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _snapshot(project) == before

    @POSIX_ONLY
    def test_first_run_hard_failure_is_propagated(self, tmp_path: Path) -> None:
        project = _server_project(tmp_path, first_run_exit=7)
        result = subprocess.run(
            [
                "bash",
                str(project / "server-setup.sh"),
                "--yes",
                "--no-memory",
                "--no-service",
                "--no-system-deps",
            ],
            cwd=project,
            env=_clean_env(),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 7, result.stdout + result.stderr
        assert "health check reported a blocking problem" in result.stdout

    @POSIX_ONLY
    def test_root_system_service_is_rejected(self, tmp_path: Path) -> None:
        project = _server_project(tmp_path)
        bin_dir = tmp_path / "bin"
        _write_executable(
            bin_dir / "id",
            """
            #!/usr/bin/env bash
            case "$*" in
              -u) echo 0 ;;
              -un) echo root ;;
              "-u root") echo 0 ;;
              *) exit 1 ;;
            esac
            """,
        )

        result = subprocess.run(
            [
                "bash",
                str(project / "server-setup.sh"),
                "--yes",
                "--no-memory",
                "--install-service",
                "--no-system-deps",
            ],
            cwd=project,
            env=_clean_env(bin_dir),
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "Refusing to create a systemd service that runs as root" in result.stdout
        assert not (project / "white-salary.service").exists()

    @POSIX_ONLY
    def test_sudo_setup_restores_venv_and_config_ownership(self, tmp_path: Path) -> None:
        project = _server_project(tmp_path)
        bin_dir = tmp_path / "bin"
        chown_log = tmp_path / "chown.log"
        _write_executable(
            bin_dir / "id",
            """
            #!/usr/bin/env bash
            case "$*" in
              -u) echo 0 ;;
              "-u alice") echo 1000 ;;
              "-gn alice") echo staff ;;
              *) exit 1 ;;
            esac
            """,
        )
        _write_executable(
            bin_dir / "chown",
            f"#!/usr/bin/env bash\nprintf '%s\n' \"$*\" >> {chown_log}\n",
        )
        env = _clean_env(bin_dir)
        env["SUDO_USER"] = "alice"

        result = subprocess.run(
            [
                "bash",
                str(project / "server-setup.sh"),
                "--yes",
                "--no-memory",
                "--no-service",
                "--no-system-deps",
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        calls = chown_log.read_text(encoding="utf-8").splitlines()
        assert "-R alice:staff .venv" in calls
        assert "alice:staff conf.yaml" in calls

    @POSIX_ONLY
    @pytest.mark.parametrize("directory_name", ["white-salary", "white salary server"])
    def test_generated_service_passes_systemd_verify(
        self, tmp_path: Path, directory_name: str
    ) -> None:
        analyzer = shutil.which("systemd-analyze")
        if not analyzer:
            pytest.skip("systemd-analyze unavailable")
        project = tmp_path / directory_name
        python_path = project / ".venv" / "bin" / "python"
        python_path.parent.mkdir(parents=True)
        python_path.symlink_to(Path(sys.executable))
        (project / "run_server.py").write_text("# fixture\n", encoding="utf-8")

        text = (PROJECT_ROOT / "server-setup.sh").read_text(encoding="utf-8")
        start = text.index("systemd_path() {")
        end = text.index("determine_service_user() {")
        function_block = text[start:end]
        driver = tmp_path / f"write-{directory_name.replace(' ', '-')}.sh"
        driver.write_text(
            function_block
            + "\nPROJECT_ROOT=\"$1\"\n"
            + "HOST_VALUE=0.0.0.0\nPORT_VALUE=12400\n"
            + "write_service_file \"$(id -un)\"\n",
            encoding="utf-8",
        )

        subprocess.run(["bash", str(driver), str(project)], cwd=tmp_path, check=True)
        unit = tmp_path / "white-salary.service"
        result = subprocess.run(
            [analyzer, "verify", str(unit)], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stdout + result.stderr
        unit_text = unit.read_text(encoding="utf-8")
        escaped_project = str(project).replace("%", "%%").replace(" ", r"\x20")
        assert f"WorkingDirectory={escaped_project}" in unit_text
        assert f'ExecStart="{python_path}" "{project / "run_server.py"}"' in unit_text
