from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import get_current_user, get_graph
from backend.models import User
from backend.organize.graph import Outcome, is_paused, resume_organize, start_organize

router = APIRouter(prefix="/api/organize", tags=["organize"])


class OrganizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class AnswersRequest(BaseModel):
    # question id -> answer text, or null/empty when skipped
    answers: dict[str, str | None] = Field(default_factory=dict, max_length=10)


def _own_thread(thread_id: str, user: User) -> None:
    # Thread ids start with the owner's id, so one user cannot resume another's run.
    if not thread_id.startswith(f"{user.id}:"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organize session")


@router.post("", response_model=Outcome)
def organize(body: OrganizeRequest, user: User = Depends(get_current_user), graph=Depends(get_graph)) -> Outcome:
    if not body.text.strip():
        raise HTTPException(422, "Text is empty")
    return start_organize(graph, f"{user.id}:{uuid4().hex}", body.text.strip(), user.team_id)


@router.post("/{thread_id}/answers", response_model=Outcome)
def answer(
    thread_id: str,
    body: AnswersRequest,
    user: User = Depends(get_current_user),
    graph=Depends(get_graph),
) -> Outcome:
    _own_thread(thread_id, user)
    if not is_paused(graph, thread_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This session is not waiting for answers")
    return resume_organize(graph, thread_id, body.answers)
