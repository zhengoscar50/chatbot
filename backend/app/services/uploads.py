"""Reading an uploaded file without trusting how big it is.

`await file.read()` with no argument buffers the whole upload into memory
before anything else runs. On an authenticated route that is a bounded risk;
on the public share route it is not, because the caller is a stranger on
somebody else's website and nothing about the request had to be true.

Content-Length is a claim, not a fact — it is set by the client and a hostile
one can understate it or omit it. So it is used only as a cheap early refusal
for honest callers, and the real limit is enforced while reading.
"""
from fastapi import HTTPException, UploadFile

# 64 KiB: large enough that the loop is not the cost, small enough that an
# oversized upload is refused after buffering kilobytes rather than gigabytes.
CHUNK = 64 * 1024

TOO_LARGE = "That file is too large. The limit is {mb} MB."


async def read_upload_capped(file: UploadFile, limit: int) -> bytes:
    """The file's bytes, or 413 as soon as it exceeds `limit`.

    Reads in chunks and stops at the first one that crosses the line, so the
    process never holds more than the limit plus one chunk — the point being
    that refusing a 2 GB upload must not require receiving 2 GB first.
    """
    mb = max(1, limit // (1024 * 1024))

    # An honest client's Content-Length lets us refuse before reading anything.
    declared = file.size
    if declared is not None and declared > limit:
        raise HTTPException(status_code=413, detail=TOO_LARGE.format(mb=mb))

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            # Stop reading. Whatever remains on the wire is the client's
            # problem; we are not going to buffer it to find out how much.
            raise HTTPException(status_code=413, detail=TOO_LARGE.format(mb=mb))
        chunks.append(chunk)
    return b"".join(chunks)
