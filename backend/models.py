from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Team(SQLModel, table=True):
    # Slug ids ("payments") instead of integers: they are what the LLM's JSON
    # schema enum and the gold labels refer to, so they must stay readable.
    id: str = Field(primary_key=True)
    name: str
    description: str = ""
    owns: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    password_hash: str
    team_id: str = Field(foreign_key="team.id")
