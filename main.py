
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.email_workflow import build_email_workflow
from apis.banking_api import router as banking_router
from apis.email_api import poll_mailbox
from apis.email_api import router as email_router
from utils import mailbox
from utils.database import initialize_database
from utils.email_database import initialize_email_database
from utils.logging_config import setup_logging

# Resolve the project root so the checkpoint database
# is created in the same directory as main.py.
setup_logging()

PROJECT_ROOT = Path(__file__).resolve().parent
CHECKPOINT_DB_PATH = Path(os.environ.get("DATA_DIR") or PROJECT_ROOT) / "email_checkpoints.db"


logger = logging.getLogger(__name__)


async def mailbox_poll_loop(app: FastAPI, seconds: int):
    """Check the real mailbox every `seconds`. A failed poll is logged and retried next time."""
    while True:
        try:
            results = await poll_mailbox(app.state.email_workflow)
            if results:
                logger.info("Mailbox poll processed %d message(s)", len(results))
        except Exception:  # noqa: BLE001
            logger.exception("Mailbox poll failed")
        await asyncio.sleep(seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize databases and the persistent LangGraph workflow
    when FastAPI starts. Keep the checkpoint connection open
    until the application shuts down.
    """

    if not os.environ.get("REVIEWER_TOKEN"):
        logger.warning("REVIEWER_TOKEN is not set: anyone who can reach this port can approve and send replies.")

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

        # Optional background polling of the real mailbox (MAIL_POLL_SECONDS=0 turns it off).
        poll_seconds = int(os.environ.get("MAIL_POLL_SECONDS", "0"))
        poller = None
        if poll_seconds > 0 and mailbox.configured():
            poller = asyncio.create_task(mailbox_poll_loop(app, poll_seconds))

        # FastAPI serves requests while execution is paused here.
        yield

        if poller:
            poller.cancel()

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


@app.get("/ui", include_in_schema=False)
def reviewer_console():
    """The reviewer console. The page itself holds no data; its API calls need the reviewer token."""
    return FileResponse(PROJECT_ROOT / "ui" / "index.html")


@app.get("/health")
def health_check():
    """Check whether the API is running."""
    return {"status": "ok"}
