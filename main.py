import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bland-voice-agent")

BLAND_API_KEY = os.getenv("BLAND_API_KEY", "")
BLAND_BASE_URL = os.getenv("BLAND_BASE_URL", "https://api.bland.ai").rstrip("/")
CUSTOM_VOICE_ID = os.getenv("CUSTOM_VOICE_ID", "13a1a524-4515-4f96-a57a-aa142697972e")  # Your Bland custom voice ID

app = FastAPI(title="Bland AI Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CALL_LOGS: List[Dict[str, Any]] = []
CALL_STATE: Dict[str, Dict[str, Any]] = {}

class CallRequest(BaseModel):
    phone_number: str = Field(..., description="India number in E.164 format. Example: +919876543210")
    first_sentence: str = Field(default="Hello! This is Arun from Aero Services. I am calling for a demo AI voice call.")
    task: Optional[str] = Field(default=None, description="Full instruction for the AI agent")


def _headers() -> Dict[str, str]:
    if not BLAND_API_KEY:
        raise HTTPException(status_code=500, detail="BLAND_API_KEY is missing. Add it in .env or Render Environment Variables.")
    return {
        "Authorization": BLAND_API_KEY,
        "Content-Type": "application/json",
    }


def _safe_error(response: requests.Response) -> str:
    try:
        return str(response.json())
    except Exception:
        return response.text or response.reason


def _find_call_log(call_id: str) -> Optional[Dict[str, Any]]:
    for log in CALL_LOGS:
        if log.get("call_id") == call_id:
            return log
    return None


def _normalize_status(raw_status: Any) -> str:
    status = str(raw_status or "UNKNOWN").lower().replace(" ", "_").replace("-", "_")
    if status in {"completed", "complete", "done", "ended", "success"}:
        return "COMPLETED"
    if status in {"no_answer", "noanswer", "not_answered", "unanswered", "missed"}:
        return "NO ANSWER"
    if status in {"busy", "user_busy"}:
        return "BUSY"
    if status in {"failed", "error", "canceled", "cancelled"}:
        return "FAILED"
    if status in {"declined", "rejected", "call_rejected"}:
        return "REJECTED"
    if status in {"queued", "scheduled", "created"}:
        return "QUEUED"
    if status in {"ringing", "initiated", "calling"}:
        return "RINGING"
    if status in {"in_progress", "inprogress", "live", "active", "answered"}:
        return "IN PROGRESS"
    return str(raw_status or "UNKNOWN").upper()


def _get_nested(data: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current:
            return current
    return None


def _extract_transcript(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Support multiple Bland response formats without crashing."""
    transcript = _get_nested(
        data,
        "transcripts",
        "transcript",
        "conversation",
        "messages",
        "call.transcripts",
        "call.transcript",
        "call.messages",
        "data.transcripts",
        "data.transcript",
        "data.messages",
    )

    lines: List[Dict[str, str]] = []
    if isinstance(transcript, list):
        for item in transcript:
            if isinstance(item, dict):
                speaker = item.get("speaker") or item.get("role") or item.get("user") or item.get("from") or "speaker"
                text = item.get("text") or item.get("message") or item.get("content") or item.get("transcript") or ""
                if text:
                    lines.append({"speaker": str(speaker), "text": str(text)})
            elif isinstance(item, str) and item.strip():
                lines.append({"speaker": "conversation", "text": item.strip()})
    elif isinstance(transcript, str) and transcript.strip():
        for row in transcript.splitlines():
            if row.strip():
                lines.append({"speaker": "conversation", "text": row.strip()})

    # Some responses keep final summary/analysis separately.
    summary = _get_nested(data, "summary", "call.summary", "data.summary", "analysis.summary")
    if summary and not lines:
        lines.append({"speaker": "summary", "text": str(summary)})

    return lines


def _duration_seconds(data: Dict[str, Any]) -> Optional[int]:
    value = _get_nested(data, "duration", "call_length", "answered_by_duration", "data.duration", "call.duration")
    try:
        return int(float(value)) if value is not None else None
    except Exception:
        return None


@app.get("/api/health")
def health():
    return {"status": "running", "time": datetime.now().isoformat()}


@app.post("/api/call")
def create_call(payload: CallRequest):
    phone = payload.phone_number.strip()
    if not phone.startswith("+"):
        raise HTTPException(status_code=400, detail="Use E.164 format. For India: +91XXXXXXXXXX")

    task = payload.task or (
        "You are a polite AI voice agent named Arun. "
        "Start with the first sentence. Speak naturally in simple English/Hinglish. "
        "This is only a demo call. Do not ask for OTP, card details, Aadhaar, PAN, or sensitive information. "
        "If the person answers, continue a short natural conversation. "
        "If the person is busy, says wrong number, or wants to stop, apologize and end politely."
    )

    bland_payload: Dict[str, Any] = {
        "phone_number": phone,
        "task": task,
        "first_sentence": payload.first_sentence,
        "voice": CUSTOM_VOICE_ID,
        "language": "en-IN",
        "wait_for_greeting": True,
        "record": False,
        "max_duration": 90,
    }

    try:
        response = requests.post(
            f"{BLAND_BASE_URL}/v1/calls",
            headers=_headers(),
            json=bland_payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.exception("Bland connection failed")
        raise HTTPException(status_code=502, detail=f"Bland connection failed: {exc}")

    if response.status_code >= 400:
        error_text = _safe_error(response)
        logger.error("Bland API error %s: %s", response.status_code, error_text)
        raise HTTPException(status_code=response.status_code, detail=error_text)

    data = response.json()
    call_id = data.get("call_id") or data.get("id") or data.get("c_id") or data.get("callId") or "unknown"
    status = _normalize_status(data.get("status") or data.get("call_status") or "QUEUED")

    log = {
        "created_at": datetime.now().strftime("%d/%m/%Y, %I:%M:%S %p"),
        "call_id": call_id,
        "number": phone,
        "voice": "Custom Voice",
        "duration": "-",
        "status": status,
        "transcript": [],
        "raw": data,
    }
    CALL_LOGS.insert(0, log)
    CALL_STATE[call_id] = log

    return {"success": True, "message": "Call initiated", "call": log}


@app.get("/api/call/{call_id}/live")
def get_call_live(call_id: str):
    """Poll Bland for status + transcript. UI uses this for live_conversation_stream.log."""
    if call_id == "unknown":
        raise HTTPException(status_code=400, detail="Bland did not return a valid call id.")

    try:
        response = requests.get(
            f"{BLAND_BASE_URL}/v1/calls/{call_id}",
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch call status: {exc}")

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_safe_error(response))

    data = response.json()
    raw_status = (
        data.get("status")
        or data.get("call_status")
        or data.get("queue_status")
        or _get_nested(data, "call.status", "data.status")
        or "UNKNOWN"
    )
    status = _normalize_status(raw_status)
    transcript = _extract_transcript(data)
    duration = _duration_seconds(data)

    # Bland sometimes returns machine/human disposition separately.
    answered_by = data.get("answered_by") or _get_nested(data, "call.answered_by", "data.answered_by")
    if answered_by:
        answered_by_low = str(answered_by).lower()
        if "no" in answered_by_low and "answer" in answered_by_low:
            status = "NO ANSWER"
        elif "busy" in answered_by_low:
            status = "BUSY"

    log = _find_call_log(call_id)
    if log:
        log["status"] = status
        log["transcript"] = transcript
        log["duration"] = f"{duration}s" if duration is not None else log.get("duration", "-")
        log["raw"] = data

    return {
        "call_id": call_id,
        "status": status,
        "duration": f"{duration}s" if duration is not None else "-",
        "answered_by": answered_by,
        "transcript": transcript,
        "raw": data,
    }


@app.get("/api/logs")
def get_logs():
    return {"logs": CALL_LOGS[:50]}


@app.delete("/api/logs")
def clear_logs():
    CALL_LOGS.clear()
    CALL_STATE.clear()
    return {"success": True}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
