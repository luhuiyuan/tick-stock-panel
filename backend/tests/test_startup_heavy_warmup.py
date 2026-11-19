from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings


def test_startup_heavy_warmup_env_switch():
    with TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text("STARTUP_HEAVY_WARMUP=false\n", encoding="utf-8")
        settings = Settings(_env_file=env)
    assert settings.startup_heavy_warmup is False
