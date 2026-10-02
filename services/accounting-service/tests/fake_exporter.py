"""Stand-in for tools/moze-realm-export in tests: copies a prepared JSON document to --out.

argv: <prepared.json> <exit code> <stderr message> <zip> --out <json> --work <dir>
"""

import shutil
import sys


def main(argv: list[str]) -> int:
    prepared, exit_code, message, *rest = argv
    if int(exit_code):
        print(message, file=sys.stderr)
        return int(exit_code)
    shutil.copyfile(prepared, rest[rest.index("--out") + 1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
