from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class AdminProfile(BaseModel):
    email: str


class LoginResponse(BaseModel):
    email: str
