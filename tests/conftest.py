import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.config import Config, ROOT, _ConfigLoader  # noqa: E402
import yaml  # noqa: E402


@pytest.fixture
def cfg():
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        return Config(yaml.load(fh, Loader=_ConfigLoader))


@pytest.fixture
def db(tmp_path):
    from tracker.storage import Database

    return Database(tmp_path / "t.db")
