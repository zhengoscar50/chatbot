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
