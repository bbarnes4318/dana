from __future__ import annotations
from fastapi import FastAPI, HTTPException, Path as FastPath
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import os
import uuid
import logging
from dana.providers.provider_registry import registry
from dana.config.voice_config import VoiceConfig
from storage.repository import Repository
from integrations.crm_webhooks import emit_crm_event_async

logger = logging.getLogger(__name__)

app = FastAPI(title="Dana Voice Engine API", version="1.0.0")
repo = Repository()

class CallStartRequest(BaseModel):
    phone_number: str
    campaign_id: Optional[str] = "default"
    contact_id: Optional[str] = "unknown"
    provider_config: Optional[Dict[str, Any]] = Field(default_factory=dict)
    prompt_template: Optional[str] = None
    transfer_number: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class CallTransferRequest(BaseModel):
    destination: str
    warm: Optional[bool] = False

@app.post("/health")
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "dana-voice-engine"}

@app.get("/providers")
async def get_providers():
    return {
        "llm": ["local_vllm", "openai", "anthropic", "google_gemini", "deepseek", "groq", "together", "fireworks", "openrouter"],
        "tts": ["local_kokoro", "elevenlabs", "cartesia", "deepgram_aura", "openai_tts", "playht"],
        "stt": ["local_faster_whisper", "deepgram", "assemblyai", "speechmatics", "openai_whisper"],
        "vad": ["silero", "livekit_vad", "semantic_turn_detector"],
        "telephony": ["livekit_sip", "freeswitch_sip", "hopwhistle_provider"]
    }

@app.post("/calls/start")
async def start_call(payload: CallStartRequest):
    call_id = str(uuid.uuid4())
    logger.info(f"API start_call requested for number={payload.phone_number} call_id={call_id}")
    
    config = VoiceConfig()
    telephony_name = config.telephony_provider
    telephony = registry.get_telephony(telephony_name)
    if not telephony:
        raise HTTPException(status_code=400, detail=f"Telephony provider '{telephony_name}' not found")
        
    try:
        await emit_crm_event_async(
            "call.started",
            repository=repo,
            call_id=call_id,
            campaign_id=payload.campaign_id,
            phone_e164=payload.phone_number,
            lead_profile=payload.metadata
        )
        
        dial_res = await telephony.originate_call(
            destination=payload.phone_number,
            room_name=call_id,
            metadata=payload.metadata
        )
        
        await repo.save_live_call_session(
            call_id=call_id,
            campaign_id=payload.campaign_id,
            lead_id=payload.contact_id,
            phone_e164=payload.phone_number,
            status="dialing",
            metadata=payload.metadata
        )
        
        return {
            "success": True,
            "call_id": call_id,
            "dial_result": str(dial_res)
        }
    except Exception as e:
        logger.error(f"Error starting outbound call: {e}")
        await emit_crm_event_async(
            "call.failed",
            repository=repo,
            call_id=call_id,
            campaign_id=payload.campaign_id,
            phone_e164=payload.phone_number
        )
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/calls/{call_id}/end")
async def end_call(call_id: str):
    logger.info(f"API end_call requested for call_id={call_id}")
    config = VoiceConfig()
    telephony = registry.get_telephony(config.telephony_provider)
    if not telephony:
        raise HTTPException(status_code=400, detail="Telephony provider not found")
        
    success = await telephony.end_call(call_id)
    if success:
        await emit_crm_event_async(
            "call.ended",
            repository=repo,
            call_id=call_id
        )
        return {"success": True, "message": "Call terminated successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to end call")

@app.post("/calls/{call_id}/transfer")
async def transfer_call(call_id: str, payload: CallTransferRequest):
    logger.info(f"API transfer_call requested for call_id={call_id} to={payload.destination}")
    config = VoiceConfig()
    telephony = registry.get_telephony(config.telephony_provider)
    if not telephony:
        raise HTTPException(status_code=400, detail="Telephony provider not found")
        
    await emit_crm_event_async(
        "transfer.requested",
        repository=repo,
        call_id=call_id,
        transfer={"destination": payload.destination, "warm": payload.warm}
    )
    
    success = await telephony.transfer_call(call_id, payload.destination, warm=payload.warm)
    if success:
        await emit_crm_event_async(
            "transfer.succeeded",
            repository=repo,
            call_id=call_id
        )
        return {"success": True, "message": "Call transfer initiated"}
    else:
        await emit_crm_event_async(
            "transfer.failed",
            repository=repo,
            call_id=call_id
        )
        raise HTTPException(status_code=500, detail="Failed to transfer call")

@app.get("/calls/{call_id}/status")
async def get_call_status(call_id: str):
    session = await repo.get_live_call_session(call_id)
    if not session:
        sessions = await repo.query_live_call_sessions({"call_id": call_id})
        if sessions:
            session = sessions[0]
        else:
            raise HTTPException(status_code=404, detail="Call session not found")
            
    return {"success": True, "status": session.get("status"), "data": session}
