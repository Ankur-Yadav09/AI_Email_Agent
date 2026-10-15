
from contextlib import (
    asynccontextmanager,  # lets us define startup and shutdown logic in one function.
)

from fastapi import FastAPI

from apis.banking_api import router as banking_router
from utils.database import initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()  # Startup: prepare the database

    yield  # The application runs while execution is paused here

    # Shutdown: put cleanup code here if needed

# Create the FastAPI application and register its lifespan
app = FastAPI(
    title="Intelligent Email Agent",
    description="Mock banking APIs for the email automation project",
    version="0.1.0",
    lifespan=lifespan
)

app.include_router(banking_router)
# app.include_router(banking_router) registers all the routes defined in banking_api.py.
# Without registering the router, those banking endpoints would not be available through this application.

# Simple endpoint to check whether the API is running
@app.get("/health")
def health_check():
    return {"status": "ok"}
