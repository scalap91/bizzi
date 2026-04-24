"""api/routes/tools.py — Hub des 5 outils universels"""
from fastapi import APIRouter
from api.routes.tools_chat      import router as chat_router
from api.routes.tools_email     import router as email_router
from api.routes.tools_poster    import router as poster_router
from api.routes.tools_phone     import router as phone_router
from api.routes.tools_complaint import router as complaint_router
from api.routes.tools_rgpd         import router as rgpd_router
from api.routes.tools_knowledge    import router as knowledge_router
from api.routes.tools_mail_config  import router as mail_config_router
from api.routes.tools_mail_onboarding import router as mail_onboarding_router
from api.routes.tools_escalation    import router as escalation_router

router = APIRouter()

router.include_router(chat_router,            prefix="/chat",            tags=["Chat"])
router.include_router(email_router,           prefix="/email",           tags=["Email"])
router.include_router(poster_router,          prefix="/poster",          tags=["Affiches"])
router.include_router(phone_router,           prefix="/phone",           tags=["Téléphone"])
router.include_router(complaint_router,       prefix="/complaint",       tags=["Plaintes"])
router.include_router(rgpd_router,            prefix="/rgpd",            tags=["RGPD"])
router.include_router(knowledge_router,       prefix="/knowledge",       tags=["Compétences"])
router.include_router(mail_config_router,     prefix="/mail",            tags=["Mail"])
router.include_router(mail_onboarding_router, prefix="/mail/onboarding", tags=["Mail Onboarding"])
router.include_router(escalation_router,     prefix="/escalation",      tags=["Escalade"])

@router.get("/")
async def list_tools():
    return {
        "tools": [
            {"id": "chat",      "name": "Chat visiteur",      "endpoint": "/api/tools/chat"},
            {"id": "email",     "name": "Email automatique",  "endpoint": "/api/tools/email"},
            {"id": "poster",    "name": "Génération affiches","endpoint": "/api/tools/poster"},
            {"id": "phone",     "name": "Réponse téléphone",  "endpoint": "/api/tools/phone"},
            {"id": "complaint", "name": "Gestion plaintes",   "endpoint": "/api/tools/complaint"},
            {"id": "rgpd",      "name": "Droits RGPD",        "endpoint": "/api/tools/rgpd"},
        ]
    }
