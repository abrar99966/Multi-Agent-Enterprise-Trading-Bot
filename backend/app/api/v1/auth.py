from fastapi import APIRouter

router = APIRouter()

@router.post("/login")
async def login():
    return {"message": "Login endpoint stub"}

@router.post("/register")
async def register():
    return {"message": "Register endpoint stub"}
