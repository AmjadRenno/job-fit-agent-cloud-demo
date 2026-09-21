from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from backend.app.dashboard.api import AuthDep, SessionDep, service
from backend.app.dashboard.schemas import JobRead
from backend.app.search import JobSearchService, SearchJobsQuery

router = APIRouter(prefix="/api/search", tags=["search"])
class Facet(BaseModel): model_config=ConfigDict(extra="forbid"); value:str; count:int=Field(ge=0)
class SearchResponse(BaseModel):
    model_config=ConfigDict(extra="forbid")
    items:list[JobRead]; total:int=Field(ge=0); facets:dict[str,list[Facet]]; query:dict[str,str|None]
@router.get("/jobs", response_model=SearchResponse)
def jobs(_:AuthDep, session:SessionDep, q:str|None=Query(None,max_length=200), company:str|None=Query(None,max_length=120), location:str|None=Query(None,max_length=120), technology:str|None=Query(None,max_length=200), decision:str|None=Query(None,pattern="^(APPLY|CONSIDER|LOW_PRIORITY|SKIP)$"), confidence:str|None=Query(None,pattern="^(HIGH|MEDIUM|LOW)$")):
    query=SearchJobsQuery(q=q,company=company,location=location,technology=technology,decision=decision,confidence=confidence)
    items, facets=JobSearchService(service(session)).search(query)
    return SearchResponse(items=items,total=len(items),facets=facets,query=query.__dict__)
