import asyncio
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import TemplateNotFound, ChoiceLoader, FileSystemLoader
from boto3.dynamodb.conditions import Key
from decimal import Decimal
import json
import os
import logging
import traceback
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# --- Helper Class ---
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


from dotenv import load_dotenv


# --- Helpers ---
def _get_ssm_parameter(parameter_name: str, region_name: str) -> str | None:
    """Fetch a parameter value from AWS SSM Parameter Store, or None on failure."""
    try:
        ssm_client = boto3.client("ssm", region_name=region_name)
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        value = response.get("Parameter", {}).get("Value")
        if value:
            logger.info(f"Loaded SSM parameter: {parameter_name}")
            return value
        logger.warning(f"SSM parameter empty: {parameter_name}")
        return None
    except Exception as exc:
        logger.warning(f"Unable to load SSM parameter '{parameter_name}': {exc}")
        return None


# --- Configuration ---
load_dotenv()

AWS_REGION = "us-west-2"
STAGE = "stage"

# Load from .env / process env first
ENVIRONMENT = os.getenv("environment")
PROJECT = os.getenv("project")

# Fallback to SSM if missing
if not ENVIRONMENT:
    env_param = os.getenv("ENVIRONMENT_SSM_PARAM", "/travoiq/environment")
    ENVIRONMENT = _get_ssm_parameter(env_param, AWS_REGION)
if not PROJECT:
    proj_param = os.getenv("PROJECT_SSM_PARAM", "/travoiq/project")
    PROJECT = _get_ssm_parameter(proj_param, AWS_REGION)

# Final defaults
ENVIRONMENT = ENVIRONMENT or "stage"
PROJECT = PROJECT or "travoiq"
TABLE_PREFIX = f"{ENVIRONMENT}-{PROJECT}-recording-"

DETAILS_TABLE_NAME = f"{TABLE_PREFIX}contactDetails"
CUSTOMER_TRANSCRIPT_TABLE_NAME = f"{TABLE_PREFIX}contactTranscriptSegments"
AGENT_TRANSCRIPT_TABLE_NAME = f"{TABLE_PREFIX}contactTranscriptSegmentsToCustomer"

print("DETAILS_TABLE_NAME:", DETAILS_TABLE_NAME)
print("CUSTOMER_TRANSCRIPT_TABLE_NAME:", CUSTOMER_TRANSCRIPT_TABLE_NAME)
print("AGENT_TRANSCRIPT_TABLE_NAME:", AGENT_TRANSCRIPT_TABLE_NAME)

print("Current working directory:", os.getcwd())
# --- Initialization ---
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)
logger.info(f"Using templates directory: {templates.directory}")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

details_table = dynamodb.Table(DETAILS_TABLE_NAME)
customer_table = dynamodb.Table(CUSTOMER_TRANSCRIPT_TABLE_NAME)
agent_table = dynamodb.Table(AGENT_TRANSCRIPT_TABLE_NAME)

# --- API Endpoints ---
# (The / and /details endpoints are unchanged and correct)


@app.get("/api/health-check")
async def health_check():
    """Health check endpoint to verify the service is running."""
    logger.info("Health check requested")
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except TemplateNotFound:
        logger.warning("index.html not found. Serving JSON fallback for '/'.")
        return JSONResponse(
            status_code=200, content={"status": "ok", "message": "UI not available"}
        )


@app.get("/details/{call_id}")
async def get_contact_details(call_id: str):
    logger.info(f"Attempting to fetch details for call_id: {call_id}")
    try:
        # Note: get_item is also case-sensitive, but we got it right here.
        response = details_table.get_item(Key={"contactId": call_id})
        if "Item" not in response:
            raise HTTPException(
                status_code=404, detail=f"Call ID '{call_id}' not found."
            )
        logger.info(f"Successfully found details for call_id: {call_id}")
        return json.loads(json.dumps(response["Item"], cls=DecimalEncoder))
    except ClientError as e:
        logger.error(
            f"AWS ClientError on get_contact_details: {e.response['Error']['Message']}"
        )
        raise HTTPException(
            status_code=500, detail="Server Configuration or AWS Error."
        )


# --- THE CORRECTED WEBSOCKET FUNCTION ---
@app.websocket("/ws/{call_id}")
async def websocket_endpoint(websocket: WebSocket, call_id: str):
    await websocket.accept()
    logger.info(f"WebSocket connection established for Call ID: {call_id}")

    sent_segment_ids = set()

    try:
        while True:
            # --- FIX: Use 'ContactId' (Capital C, Capital I) to match the table's key schema ---
            customer_response = customer_table.query(
                KeyConditionExpression=Key("ContactId").eq(call_id)
            )
            agent_response = agent_table.query(
                KeyConditionExpression=Key("ContactId").eq(call_id)
            )

            customer_segments = customer_response.get("Items", [])
            for seg in customer_segments:
                seg["speaker"] = "Customer"

            agent_segments = agent_response.get("Items", [])
            for seg in agent_segments:
                seg["speaker"] = "Agent"

            all_segments = customer_segments + agent_segments
            all_segments.sort(key=lambda x: x.get("LoggedOn", ""))

            new_segments_found = 0
            for segment in all_segments:
                segment_id = segment.get("SegmentId")

                if segment_id and segment_id not in sent_segment_ids:
                    new_segments_found += 1
                    await websocket.send_json(
                        {
                            "speaker": segment["speaker"],
                            "text": segment.get("Transcript", ""),
                        }
                    )
                    sent_segment_ids.add(segment_id)

            if new_segments_found > 0:
                logger.info(
                    f"Found and sent {new_segments_found} new segments for call {call_id}."
                )

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for Call ID: {call_id}")
    except Exception as e:
        logger.error(
            f"An error occurred in WebSocket for call {call_id}: {e}", exc_info=True
        )


@app.get("/latest-call")
async def get_latest_call():
    """Scans the details table to find the most recent call."""
    logger.info("Polling for the latest call...")
    try:
        response = details_table.scan(Limit=20)
        items = response.get("Items", [])
    except ClientError as e:
        traceback.print_exc()
        logger.error(f"AWS Error while polling for latest call: {e}")
        raise HTTPException(status_code=500, detail="AWS Error")
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Generic error while polling for latest call: {e}")
        raise HTTPException(status_code=500, detail="Server Error")

    if not items:
        return JSONResponse(
            status_code=404, content={"detail": "No recent calls found."}
        )

    # Sort the items by timestamp (descending) to find the most recent one
    items.sort(key=lambda x: x.get("callTimestamp", ""), reverse=True)

    latest_call = items[0]
    contact_id = latest_call.get("contactId")
    logger.info(f"Found latest call with ID: {contact_id}")
    return {"contactId": contact_id}
