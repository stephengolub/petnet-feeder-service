import os
import logging
from logging.config import dictConfig
import asyncio
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from feeder import settings
from feeder.api.routers import kronos, feeder
from feeder.config import LOGGING_CONFIG
from feeder.util.mqtt import FeederClient, FeederBroker
from feeder.util.mkcert import generate_self_signed_certificate
from feeder.database.session import db
from feeder.database.models import KronosDevices


def handle_exception(loop, context):
    if 'exception' in context:
        logging.error("Caught global exception:", exc_info = context["exception"])
    else:
        msg = context["message"]
        logging.error(f"Caught global exception: {msg}")

logger = logging.getLogger("feeder")
if settings.debug:
    for named_logger in LOGGING_CONFIG["loggers"]:
        if named_logger:
            LOGGING_CONFIG["loggers"][named_logger]["level"] = "DEBUG"
    LOGGING_CONFIG["loggers"]["hbmqtt.client.plugins"] = {"level": "INFO"}
    LOGGING_CONFIG["loggers"]["hbmqtt.broker.plugins"] = {"level": "INFO"}
    LOGGING_CONFIG["loggers"]["hbmqtt.mqtt.protocol.handler"] = {"level": "INFO"}
dictConfig(LOGGING_CONFIG)

public_key = os.path.abspath(settings.mqtts_public_key)
private_key = os.path.abspath(settings.mqtts_private_key)

if not os.path.exists(public_key) and not os.path.exists(private_key):
    logger.warning("Generating self-signed key pair!")
    certificate_pair = generate_self_signed_certificate()
    with open(public_key, "wb") as f:
        logger.info("Writing new public key to %s", public_key)
        f.write(certificate_pair[0])
    with open(private_key, "wb") as f:
        logger.info("Writing new private key to %s", private_key)
        f.write(certificate_pair[1])


templates = Jinja2Templates(directory="feeder/templates")
loop = asyncio.get_event_loop()
loop.set_exception_handler(handle_exception)
client = FeederClient()
feeder.router.client = client
broker = FeederBroker()

app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version="1.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(kronos.router, prefix="/api/v1/kronos")
app.include_router(feeder.router, prefix="/api/v1/feeder")


@app.get("/", response_class=HTMLResponse)
async def read_item(request: Request):
    devices = await KronosDevices.get()
    return templates.TemplateResponse(
        "welcome.html", {"request": request, "devices": devices}
    )


@app.on_event("startup")
async def startup_event():
    await db.connect()
    loop.create_task(broker.start())
    loop.create_task(client.start())


@app.on_event("shutdown")
async def shutdown_event():
    await asyncio.gather([broker.shutdown()], return_exceptions=True)
    await db.disconnect()


if __name__ == "__main__":
    uvicorn.run("feeder.__main__:app", host="0.0.0.0", port=settings.http_port)
