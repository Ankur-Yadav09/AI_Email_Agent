
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.email_workflow import build_email_workflow
from apis.banking_api import router as banking_router
from apis.email_api import router as email_router
from utils.database import initialize_database
from utils.email_database import initialize_email_database

# Resolve the project root so the checkpoint database
# is created in the same directory as main.py.
PROJECT_ROOT = Path(__file__).resolve().parent
CHECKPOINT_DB_PATH = PROJECT_ROOT / "email_checkpoints.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize databases and the persistent LangGraph workflow
    when FastAPI starts. Keep the checkpoint connection open
    until the application shuts down.
    """

    # Initialize the existing banking and email databases.
    initialize_database()
    initialize_email_database()

    # Open the persistent SQLite checkpoint database.
    async with AsyncSqliteSaver.from_conn_string(
        str(CHECKPOINT_DB_PATH)
    ) as checkpointer: # type: ignore

        # Create checkpoint tables if they do not already exist.
        await checkpointer.setup()

        # Compile the graph with persistent checkpoint storage.
        app.state.email_workflow = build_email_workflow(checkpointer)

        # FastAPI serves requests while execution is paused here.
        yield

        # Exiting the async context closes the checkpoint connection.


# Create the FastAPI application.
app = FastAPI(
    title="Intelligent Email Agent",
    description="Mock banking APIs and email automation",
    version="0.1.0",
    lifespan=lifespan,
)

# Register existing API routers.
app.include_router(email_router)
app.include_router(banking_router)


@app.get("/health")
def health_check():
    """Check whether the API is running."""
    return {"status": "ok"}
