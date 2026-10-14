
from fastapi import FastAPI

from apis.banking_api import router as banking_router

app = FastAPI(
    title="Intelligent Email Agent",
    description="Mock banking APIs for the email automation project",
    version="0.1.0",
)
# FastAPI() creates the application instance.

app.include_router(banking_router)
# app.include_router(banking_router) registers all the routes defined in banking_api.py.
# Without registering the router, those banking endpoints would not be available through this application.

@app.get("/health")
def health_check():
    return {"status": "ok"}
