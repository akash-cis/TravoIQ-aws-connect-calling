import asyncio
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jinja2 import TemplateNotFound, ChoiceLoader, FileSystemLoader
from boto3.dynamodb.conditions import Key
from decimal import Decimal
import json
import os
import logging
import traceback
from typing import Optional, Dict
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
logger.info(
    f"Using templates directory: {os.path.join(os.path.dirname(__file__), 'templates')}"
)

# Mount static for optional self-hosted assets (e.g., amazon-connect-streams)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
else:
    logger.info(f"Static directory not found, skipping mount: {_static_dir}")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

details_table = dynamodb.Table(DETAILS_TABLE_NAME)
customer_table = dynamodb.Table(CUSTOMER_TRANSCRIPT_TABLE_NAME)
agent_table = dynamodb.Table(AGENT_TRANSCRIPT_TABLE_NAME)

# --- AWS Connect Clients and Call Manager ---
CONNECT_REGION = AWS_REGION
connect_client = boto3.client("connect", region_name=CONNECT_REGION)
# connect_contact_client = boto3.client("connectcontactlens", region_name=CONNECT_REGION)


class CallManager:
    def __init__(self) -> None:
        # These should be configured via env/SSM in real deployments
        self.instance_id = os.getenv("CONNECT_INSTANCE_ID", "your-connect-instance-id")
        flow_arn = os.getenv("CONNECT_CONTACT_FLOW_ARN")
        flow_id_env = os.getenv("CONNECT_CONTACT_FLOW_ID")
        if flow_arn and "/contact-flow/" in flow_arn:
            self.contact_flow_id = flow_arn.split("/contact-flow/")[-1]
        else:
            self.contact_flow_id = flow_id_env or "your-contact-flow-id"

        # Optional queue for outbound routing
        self.queue_id = os.getenv("CONNECT_QUEUE_ID")

        raw_source = os.getenv("CONNECT_SOURCE_PHONE", "your-connect-phone-number")
        if raw_source.startswith("+"):
            digits = "+" + "".join(ch for ch in raw_source if ch.isdigit())
        else:
            digits = "+" + "".join(ch for ch in raw_source if ch.isdigit())
        self.source_phone_number = digits

    async def initiate_outbound_call(self, agent_id: str, phone_number: str) -> str:
        try:
            params = {
                "DestinationPhoneNumber": phone_number,
                "ContactFlowId": self.contact_flow_id,
                "InstanceId": self.instance_id,
                "SourcePhoneNumber": self.source_phone_number,
                "Attributes": {"agent_id": agent_id},
            }
            if self.queue_id:
                params["QueueId"] = self.queue_id

            response = connect_client.start_outbound_voice_contact(**params)
            return response["ContactId"]
        except Exception as e:
            logger.error(f"initiate_outbound_call failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_agent_status(self, agent_id: str):
        try:
            response = connect_client.describe_user(
                UserId=agent_id, InstanceId=self.instance_id
            )
            return response
        except Exception as e:
            logger.error(f"get_agent_status failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


call_manager = CallManager()

# Active agent websocket connections
active_agent_connections: Dict[str, WebSocket] = {}


# --- Incoming Calls Broadcast Manager and API ---
class IncomingCall(BaseModel):
    contactId: str
    phoneNumber: Optional[str] = None
    customerName: Optional[str] = None
    callTimestamp: Optional[str] = None  # ISO8601
    metadata: Optional[dict] = None


class IncomingCallsManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    def connect(self, websocket: WebSocket) -> None:
        if websocket not in self.active_connections:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, message: dict) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.active_connections):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


incoming_calls_manager = IncomingCallsManager()

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


@app.get("/call", response_class=HTMLResponse)
async def call_page(request: Request):
    region = AWS_REGION
    ccp_url_env = os.getenv("CONNECT_CCP_URL")
    if ccp_url_env:
        ccp_url = ccp_url_env
    else:
        alias = os.getenv("CONNECT_INSTANCE_ALIAS", "")
        ccp_url = f"https://{alias}.my.connect.aws/ccp-v2/" if alias else "about:blank"
    return templates.TemplateResponse(
        "call.html", {"request": request, "ccpUrl": ccp_url, "region": region}
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


@app.websocket("/ws/agent/{agent_id}")
async def websocket_agent(websocket: WebSocket, agent_id: str):
    await websocket.accept()
    active_agent_connections[agent_id] = websocket
    logger.info(f"Agent websocket connected: {agent_id}")
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except Exception:
                continue
            msg_type = message.get("type")
            payload = message.get("data", {})

            if msg_type == "call_status":
                logger.info(f"Agent {agent_id} status update: {payload}")
            elif msg_type == "webrtc_offer":
                logger.info(f"Agent {agent_id} WebRTC offer received")
            elif msg_type == "webrtc_answer":
                logger.info(f"Agent {agent_id} WebRTC answer received")
            else:
                logger.info(f"Agent {agent_id} unknown message type: {msg_type}")
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        logger.info(f"Agent websocket disconnected: {agent_id}")
    finally:
        active_agent_connections.pop(agent_id, None)


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


# --- AWS Connect REST API Endpoints ---


@app.post("/api/calls/start")
async def start_call(call_data: dict):
    """Start a new outbound call via AWS Connect."""
    agent_id = call_data.get("agent_id")
    phone_number = call_data.get("phone_number")
    if not agent_id or not phone_number:
        raise HTTPException(
            status_code=400, detail="agent_id and phone_number are required"
        )
    # Accept either full ARN or bare ID for agent_id
    if ":agent/" in agent_id:
        agent_id = agent_id.split(":agent/")[-1]
    contact_id = await call_manager.initiate_outbound_call(agent_id, phone_number)
    return {"contact_id": contact_id, "status": "initiated"}


@app.post("/api/calls/{contact_id}/end")
async def end_call(contact_id: str):
    """End an active Connect call."""
    try:
        connect_client.stop_contact(
            ContactId=contact_id, InstanceId=call_manager.instance_id
        )
        return {"status": "call_ended"}
    except Exception as e:
        logger.error(f"stop_contact failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agent/{agent_id}/status")
async def get_agent_status(agent_id: str):
    if ":agent/" in agent_id:
        agent_id = agent_id.split(":agent/")[-1]
    return await call_manager.get_agent_status(agent_id)


@app.post("/api/agent/{agent_id}/status")
async def update_agent_status(agent_id: str, status_data: dict):
    try:
        if ":agent/" in agent_id:
            agent_id = agent_id.split(":agent/")[-1]
        connect_client.put_user_status(
            UserId=agent_id,
            InstanceId=call_manager.instance_id,
            AgentStatusId=status_data.get("status_id"),
        )
        return {"status": "updated"}
    except Exception as e:
        logger.error(f"put_user_status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Ingest a new incoming call and broadcast to listeners
@app.post("/api/incoming-call")
async def incoming_call(payload: IncomingCall):
    timestamp = payload.callTimestamp or datetime.now(timezone.utc).isoformat()
    item = {
        "contactId": payload.contactId,
        "callTimestamp": timestamp,
        "phoneNumber": payload.phoneNumber,
        "customerName": payload.customerName,
        "metadata": payload.metadata or {},
    }
    try:
        details_table.put_item(Item=item)
        logger.info(f"Stored incoming call: {payload.contactId}")
    except ClientError as e:
        logger.error(f"Failed to store incoming call: {e}")
        raise HTTPException(status_code=500, detail="Failed to persist incoming call")

    await incoming_calls_manager.broadcast({"type": "incoming_call", "data": item})
    return JSONResponse(
        status_code=201, content={"status": "created", "contactId": payload.contactId}
    )


@app.websocket("/ws/incoming-calls")
async def websocket_incoming_calls(websocket: WebSocket):
    await websocket.accept()
    incoming_calls_manager.connect(websocket)
    logger.info("WebSocket connected for incoming calls stream")
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        incoming_calls_manager.disconnect(websocket)
        logger.info("WebSocket disconnected for incoming calls stream")
