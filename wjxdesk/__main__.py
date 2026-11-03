"""支持 `python -m wjxdesk`。"""

from __future__ import annotations

import sys

from wjxdesk.cli import main

if __name__ == "__main__":
    sys.exit(main())
