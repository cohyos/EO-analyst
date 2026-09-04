"""`python -m eoa.api` -- runs uvicorn on `settings().api.host`/`port`."""

from __future__ import annotations

import uvicorn

from eoa.config import settings


def main() -> None:
    s = settings()
    uvicorn.run("eoa.api.app:app", host=s.api.host, port=s.api.port, reload=False)


if __name__ == "__main__":
    main()
