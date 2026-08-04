"""Offline master-key provisioning helper for AstrContinuum."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from .storage.crypto import StorageSecurityError
from .storage.security import fingerprint_key_file, generate_key_file

_USAGE_ERROR = "KEYCTL_USAGE_INVALID"


class _UsageError(ValueError):
    pass


class _ContentFreeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _UsageError from None


def _parser() -> argparse.ArgumentParser:
    parser = _ContentFreeArgumentParser(prog="python -m astrcontinuum.keyctl")
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_ContentFreeArgumentParser,
    )

    generate = commands.add_parser(
        "generate",
        help="create one new external key file",
    )
    generate.add_argument("--output", required=True)
    generate.add_argument("--data-dir")

    fingerprint = commands.add_parser(
        "fingerprint",
        help="print only the non-secret key id",
    )
    fingerprint.add_argument("--key-file", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the content-free offline key helper."""

    try:
        arguments = _parser().parse_args(argv)
    except _UsageError:
        sys.stderr.write(f"ERROR={_USAGE_ERROR}\n")
        return 2

    try:
        if arguments.command == "generate":
            generated = generate_key_file(
                arguments.output,
                data_dir=arguments.data_dir,
            )
            permission_state = "HARDENED" if generated.permissions_hardened else "UNVERIFIED"
            sys.stdout.write(
                f"KEY_ID={generated.key_id}\n"
                f"OUTPUT={generated.path}\n"
                f"PERMISSIONS={permission_state}\n"
            )
            return 0
        key_id = fingerprint_key_file(arguments.key_file)
        sys.stdout.write(f"KEY_ID={key_id}\n")
        return 0
    except StorageSecurityError as error:
        sys.stderr.write(f"ERROR={error.code.value}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
