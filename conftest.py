"""Root conftest: puts the shared lib/ package (medialib) on sys.path for
every test run, regardless of which subdirectory pytest is invoked against
-- e.g. `pytest skills/media-library/scripts/` alone (the documented
per-skill convention) must resolve `import medialib` the same way the CLI
shims' own sys.path bootstrap does, even though no test file under that
directory happens to import lib/ itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
