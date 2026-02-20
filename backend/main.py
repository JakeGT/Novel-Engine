from fastapi import FastAPI
from contextlib import asynccontextmanager
from backend.core.config import settings
from backend.api.endpoints import router as api_router
from backend.graph.manager import graph_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure graph constraints exist
    graph_manager.create_constraints()
    yield
    # Shutdown: Close driver connection
    graph_manager.close()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    description="Novel Engine API with Knowledge Graph and RAG",
    lifespan=lifespan
)

app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Novel Engine API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
