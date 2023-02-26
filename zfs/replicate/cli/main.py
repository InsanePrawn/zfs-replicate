"""Main function zfs-replicate."""
import click
import itertools

from typing import Optional

from .. import filesystem, snapshot, ssh, task
from ..compress import Compression
from ..filesystem import FileSystem
from ..filesystem import filesystem as filesystem_t
from ..ssh import Cipher
from .click import EnumChoice
from ..error import ZFSReplicateError


@click.command()  # type: ignore[misc]
@click.option("--verbose", "-v", is_flag=True, help="Print additional output.")  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "-n",
    "--dry-run",
    is_flag=True,
    help="Generate replication tasks but do not execute them.",
)
@click.option(  # type: ignore[misc]
    "--follow-delete",
    is_flag=True,
    help="Delete snapshots on REMOTE_FS that have been deleted from LOCAL_FS.",
)
@click.option("--recursive", is_flag=True, help="Recursively replicate snapshots.")  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--port",
    "-p",
    type=click.IntRange(1, 65535),
    metavar="PORT",
    default=22,
    help="Connect to SSH on PORT.",
)
@click.option(  # type: ignore[misc]
    "--login",
    "-l",
    "--user",
    "-u",
    "user",
    metavar="USER",
    help="Connect to SSH as USER.",
)
@click.option(  # type: ignore[misc]
    "-i",
    "--identity-file",
    type=click.Path(exists=True, dir_okay=False),
    required=False,
    help="SSH identity file to use.",
)
@click.option(  # type: ignore[misc]
    "--cipher",
    type=EnumChoice(Cipher),
    default=Cipher.STANDARD,
    help="One of: disable (no ciphers), fast (only fast ciphers), or standard (default ciphers).",
)
@click.option(  # type: ignore[misc]
    "--compression",
    type=EnumChoice(Compression),
    default=Compression.LZ4,
    help="One of: off (no compression), lz4 (fastest), pigz (all rounder), or plzip (best compression).",
)
@click.option(
    "--reset-ssh-env",
    type=bool,
    default=False,
    is_flag=True,
    help="Reset env when calling SSH to avoid ssh-agent interference",
)
@click.argument("host", required=True)  # type: ignore[misc]
@click.argument("remote_fs", type=filesystem_t, required=True, metavar="REMOTE_FS")  # type: ignore[misc]
@click.argument("local_fs", type=filesystem_t, required=True, metavar="LOCAL_FS")  # type: ignore[misc]
def main(  # pylint: disable=R0914,R0913
    verbose: bool,
    dry_run: bool,
    follow_delete: bool,
    recursive: bool,
    port: int,
    user: str,
    identity_file: Optional[str],
    cipher: Cipher,
    compression: Compression,
    reset_ssh_env: bool,
    host: str,
    remote_fs: FileSystem,
    local_fs: FileSystem,
) -> None:
    """Replicate LOCAL_FS to REMOTE_FS on HOST."""
    ssh_command = ssh.command(cipher, user, identity_file, port, host, reset_env=reset_ssh_env)

    if verbose:
        click.echo(f"checking filesystem {local_fs.name}")

    l_snaps = snapshot.list(local_fs, recursive=recursive, verbose=verbose)
    # Improvement: exclusions from snapshots to replicate.

    if verbose:
        click.echo(f"found {len(l_snaps)} snapshots on {local_fs.name}")
        click.echo()

    r_filesystem = filesystem.remote_dataset(remote_fs, local_fs)

    if not dry_run:
        filesystem.create(r_filesystem, ssh_command=ssh_command, verbose=verbose)

    if verbose:
        click.echo(f"checking filesystem {host}/{r_filesystem.name}")

    try:
        r_snaps = snapshot.list(r_filesystem, recursive=recursive, ssh_command=ssh_command, verbose=verbose)
        if verbose:
            click.echo(f"found {len(r_snaps)} snapshots on {r_filesystem.name}")
            click.echo()
    except ZFSReplicateError:
        if not dry_run:
            raise
        r_snaps = []
        if verbose:
            click.echo(f"Dataset {r_filesystem.name} doesn't exist yet")
            click.echo()

    filesystem_l_snaps = {
        filesystem: list(l_snaps)
        for filesystem, l_snaps in itertools.groupby(
            l_snaps, key=lambda x: x.filesystem
        )
    }
    filesystem_r_snaps = {
        filesystem: list(r_snaps)
        for filesystem, r_snaps in itertools.groupby(
            r_snaps, key=lambda x: x.filesystem
        )
    }
    tasks = task.generate(
        remote_fs, filesystem_l_snaps, filesystem_r_snaps, follow_delete=follow_delete
    )

    if verbose:
        click.echo(task.report(tasks))

    if not dry_run:
        filesystem_tasks = [
            (filesystem, list(tasks))
            for filesystem, tasks in itertools.groupby(
                tasks, key=lambda x: x.filesystem
            )
        ]
        task.execute(
            remote_fs,
            filesystem_tasks,
            follow_delete=follow_delete,
            compression=compression,
            ssh_command=ssh_command,
        )
