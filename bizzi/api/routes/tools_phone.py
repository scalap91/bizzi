"""api/routes/tools_phone.py"""
from fastapi import APIRouter, Form
from fastapi.responses import Response
from typing import Optional
from tools.phone.phone_agent import PhoneAgent
from config.domain_loader import DomainLoader

router = APIRouter()

@router.get("/greeting")
async def phone_greeting(domain: str = "media"):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = PhoneAgent(cfg)
    return Response(content=agent.twiml_greeting(), media_type="application/xml")

@router.post("/incoming")
async def phone_incoming(domain: str = "media", From: str = Form(""), CallSid: str = Form("")):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = PhoneAgent(cfg)
    return Response(content=agent.twiml_greeting(), media_type="application/xml")

@router.post("/handle")
async def phone_handle(
    domain:       str = "media",
    From:         str = Form(""),
    SpeechResult: str = Form(""),
    Digits:       str = Form(""),
):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = PhoneAgent(cfg)
    result = await agent.process_call(From, SpeechResult, Digits)
    return Response(content=result["twiml"], media_type="application/xml")
