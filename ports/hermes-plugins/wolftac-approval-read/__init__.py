"""wolftac-approval-read (R1): read-only Discord approval reader for the Wolftac Factory.

Registers exactly one CLI command and nothing else: no tool, hook or slash
command. Importing and registering are inert (no I/O, no credential read, no
thread). Under ``plugins.isolation: host`` Hermes never imports a user plugin
in-process, so the command does not exist there and every caller fails closed.
See reader.py for the read contract and WD-546 for the approval design.
"""
from . import reader


def register(ctx) -> None:
    ctx.register_cli_command(
        name=reader.COMMAND,
        help="Read one #approvals message and its rocket reactors (read-only JSON)",
        setup_fn=reader.setup_parser,
        handler_fn=reader.handle,
        description=(
            "Read-only. Proves Harper's bot identity, reads one message in Discord "
            "#approvals (or a thread under it) and the complete list of accounts "
            "that reacted with the rocket, and prints one JSON line. It never "
            "posts, reacts or approves anything."
        ),
    )
