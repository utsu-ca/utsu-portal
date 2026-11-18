"""Published, non-executable copy for the portal's built-in page templates."""
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..context import Context
from ..store import REV

COLLECTION = "public_content"
PageId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class PageCopy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    tagline: str = Field(max_length=280)
    summary: list[Line] = Field(min_length=1, max_length=20)
    eligibility: list[Line] = Field(max_length=20)
    steps: list[Line] = Field(max_length=20)
    deadlines: list[Line] = Field(max_length=20)


class PublishCopy(BaseModel):
    base_rev: int = Field(ge=0)
    content: PageCopy = Field(alias="copy")


class ContentModule:
    name = "content"

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api")
        admin = ctx.require_role()

        @r.get("/content")
        def published():
            return {"pages": {row["id"]: {"rev": row[REV], "copy": row["copy"]}
                              for row in ctx.store.list(COLLECTION)}}

        @r.put("/admin/content/{page}")
        def publish(page: PageId, request: PublishCopy, actor: dict[str, Any] = admin):
            previous = ctx.store.get(COLLECTION, page)
            rev = previous[REV] if previous else 0
            copy = request.content.model_dump()
            doc = {"id": page, "copy": copy, REV: rev + 1}
            if rev != request.base_rev or not (
                ctx.store.replace(COLLECTION, page, doc, rev) if previous
                else ctx.store.create(COLLECTION, page, doc)
            ):
                raise HTTPException(409, "Someone published a newer version. Copy any unsaved text you want to keep, then reload the published copy.")
            ctx.audit.record(actor["uuid"], "content.published", f"content:{page}",
                             previous["copy"] if previous else None, copy, {"rev": rev + 1})
            return {"rev": rev + 1, "copy": copy}

        return r
