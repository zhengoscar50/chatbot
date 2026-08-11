"""Make the suite hermetic.

Settings reads `.env` and `auth_jwt_secret` has no default, so on a machine
without a `.env` — a fresh clone, a server, CI — 29 tests failed with a
Pydantic "field required" error. The suite was quietly depending on whatever
credentials the developer happened to have on disk.

These values are set only when absent, so an intentional override in a shell
still wins.
"""
import os

# Set at import, not in a fixture: conftest is imported before the test modules
# are collected, and a module that builds Settings at import time would
# otherwise still fail. setdefault, so an intentional shell override wins.
for _key, _value in {
    "POWABASE_BASE_URL": "https://tests.invalid",
    "POWABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    "AUTH_JWT_SECRET": "test-jwt-secret-not-a-real-one",
}.items():
    os.environ.setdefault(_key, _value)

# Stop Settings reading the developer's .env. Supplying the three required
# credentials above was not enough: EVERY setting still fell back to .env, so a
# test asserting a default passed or failed depending on what the developer
# happened to have on disk. Setting ORCHESTRATOR_MODEL in a real .env broke two
# "default" tests that had nothing to do with the change — the suite was
# reporting the developer's machine, not the code.
from app.core.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None
