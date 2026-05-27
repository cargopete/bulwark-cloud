"""AWS Lambda handler via Mangum ASGI adapter."""
from mangum import Mangum

from .main import app

handler = Mangum(app, lifespan="off")
