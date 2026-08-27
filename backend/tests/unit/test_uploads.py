import asyncio

import pytest
from fastapi import HTTPException

from app.services.uploads import read_upload_capped


def run(coro):
    """Drive one coroutine to completion.

    This repo pins its dependencies and keeps them few, and the helper under
    test is a plain coroutine with no event-loop fixtures to arrange — so it is
    driven directly rather than adding pytest-asyncio for seven tests.
    """
    return asyncio.run(coro)


class FakeUpload:
    """An UploadFile as far as this helper is concerned.

    `size` is the client's declared Content-Length, which a hostile caller can
    understate or omit — so tests set it independently of the real payload.
    """

    def __init__(self, payload: bytes, size=None):
        self._payload = payload
        self._pos = 0
        self.size = size
        self.bytes_served = 0

    async def read(self, n=-1):
        if n is None or n < 0:
            chunk = self._payload[self._pos:]
        else:
            chunk = self._payload[self._pos:self._pos + n]
        self._pos += len(chunk)
        self.bytes_served += len(chunk)
        return chunk


def test_a_file_under_the_limit_comes_back_whole():
    f = FakeUpload(b"x" * 5000, size=5000)

    assert run(read_upload_capped(f, 10000)) == b"x" * 5000


def test_an_honest_oversized_file_is_refused_before_a_single_byte_is_read():
    """Content-Length is only a claim, but an honest one lets us refuse without
    touching the body at all."""
    f = FakeUpload(b"x" * 50000, size=50000)

    with pytest.raises(HTTPException) as e:
        run(read_upload_capped(f, 10000))

    assert e.value.status_code == 413
    assert f.bytes_served == 0


def test_a_lying_content_length_does_not_get_through():
    """The check that carries the feature. A hostile client can claim any size
    it likes, or none — so the limit has to be enforced against the bytes that
    actually arrive, not against what the request said about them."""
    f = FakeUpload(b"x" * 50000, size=10)          # claims 10 bytes, sends 50k

    with pytest.raises(HTTPException) as e:
        run(read_upload_capped(f, 10000))

    assert e.value.status_code == 413


def test_a_missing_content_length_does_not_get_through():
    f = FakeUpload(b"x" * 50000, size=None)

    with pytest.raises(HTTPException):
        run(read_upload_capped(f, 10000))


def test_it_stops_reading_instead_of_buffering_the_whole_upload():
    """The entire point. Refusing a huge upload must not require receiving it
    first — otherwise the limit documents an intention while the memory is
    already spent."""
    huge = 40 * 1024 * 1024
    f = FakeUpload(b"x" * huge, size=None)

    with pytest.raises(HTTPException):
        run(read_upload_capped(f, 1024 * 1024))

    # At most the limit plus the chunk that crossed it.
    assert f.bytes_served <= 1024 * 1024 + 64 * 1024, f.bytes_served


def test_a_file_exactly_on_the_limit_is_allowed():
    """Off-by-one on a boundary users will hit: the limit is what is allowed,
    not what is refused."""
    f = FakeUpload(b"x" * 10000, size=10000)

    assert len(run(read_upload_capped(f, 10000))) == 10000


def test_the_message_names_the_limit_in_megabytes():
    """A visitor cannot act on '413'. They can act on 'the limit is 10 MB'."""
    # Declared oversized, so it is refused on the claim alone — no need to
    # build a 10 MB payload just to read the sentence back.
    f = FakeUpload(b"x" * 50, size=20 * 1024 * 1024)

    with pytest.raises(HTTPException) as e:
        run(read_upload_capped(f, 10 * 1024 * 1024))

    assert "10 MB" in e.value.detail
