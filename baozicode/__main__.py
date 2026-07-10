"""支持 `python -m baozicode`。

必须显式传 `sys.argv[1:]` 给 main(),否则 argparse fallback 到
`sys.argv[1:]` 时会把模块名 `'baozicode'` 当成顶层 subcommand 的
`<command>` 值(因为 `python -m X` 时 sys.argv = `['-m', 'X', ...]`,
`sys.argv[1:]` 以模块名开头,而不是以 subcommand 开头)。
"""

import sys

from baozicode.cli import main

raise SystemExit(main(sys.argv[1:]))
