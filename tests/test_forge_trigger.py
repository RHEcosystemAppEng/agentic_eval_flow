import os
import subprocess

from scripts.trigger_forge_controlled import rewrite_clones


def test_fetch_retries_are_bounded_and_do_not_hide_terminal_failure(tmp_path):
    for name, script in {
        "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
        "sleep": "#!/bin/sh\nexit 0\n",
        "git": '#!/bin/sh\nn=0; [ ! -f "$FETCH_COUNT" ] || n=$(cat "$FETCH_COUNT")\n'
        'n=$((n+1)); echo "$n" > "$FETCH_COUNT"\n'
        '[ "$n" -gt "$FETCH_FAILURES" ]\n',
    }.items():
        path = tmp_path / name
        path.write_text(script)
        path.chmod(0o755)
    script = rewrite_clones('git fetch origin "pinned-ref" --depth 1')
    for failures, expected_code in ((2, 0), (9, 1)):
        count = tmp_path / str(failures)
        result = subprocess.run(
            ["bash", "-euc", script],
            capture_output=True,
            env={
                **os.environ,
                "PATH": str(tmp_path) + ":" + os.environ["PATH"],
                "FETCH_COUNT": str(count),
                "FETCH_FAILURES": str(failures),
            },
        )
        assert result.returncode == expected_code
        assert count.read_text().strip() == "3"


def test_checkout_accepts_commit_and_branch(tmp_path):
    repo = tmp_path / "origin"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "fixture").write_text("stable")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Eval",
            "-c",
            "user.email=eval@example.test",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    # macOS may lack GNU timeout; use a local forwarding shim for this test.
    shim = tmp_path / "timeout"
    shim.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    shim.chmod(0o755)
    import os

    for n, ref in enumerate(["main", sha]):
        dest = tmp_path / str(n)
        script = rewrite_clones(f'git clone --depth 1 --branch "{ref}" \\\n  "{repo}" "{dest}"')
        subprocess.run(
            ["bash", "-euc", script],
            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]},
            check=True,
            capture_output=True,
        )
        assert (dest / "fixture").read_text() == "stable"
        assert subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip() == sha
        # Existing pinned checkout must remain usable with origin unavailable.
        subprocess.run(["git", "-C", str(dest), "remote", "set-url", "origin", str(tmp_path / "gone")], check=True)
        same = rewrite_clones(
            f'git -C "{dest}" fetch origin "{sha}" --depth 1\n'
            f'git -C "{dest}" -c advice.detachedHead=false checkout FETCH_HEAD'
        )
        subprocess.run(
            ["bash", "-euc", same],
            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]},
            check=True,
            capture_output=True,
        )
