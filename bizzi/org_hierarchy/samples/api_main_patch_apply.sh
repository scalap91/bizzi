#!/usr/bin/env bash
# Application du wiring api/main.py — à exécuter par Pascal.
# Idempotent : check si les imports/routes sont déjà présents avant d'ajouter.
#
# Usage :
#   sudo bash bizzi/org_hierarchy/samples/api_main_patch_apply.sh
#
set -euo pipefail

MAIN=/opt/bizzi/bizzi/api/main.py
[[ -f "$MAIN" ]] || { echo "✗ $MAIN introuvable"; exit 1; }

cp -a "$MAIN" "$MAIN.bak-$(date +%Y%m%d-%H%M%S)"

# 1) Imports (après les imports phone/social existants)
if ! grep -q "from org_hierarchy import routes as org_routes" "$MAIN"; then
    sed -i '/^from social import routes as social_routes$/a from org_hierarchy import routes as org_routes\nfrom org_hierarchy.embed import embed_router as org_embed_router' "$MAIN"
    echo "✓ imports org_hierarchy ajoutés"
else
    echo "= imports déjà présents (skip)"
fi

# 2) Routers (après le include phone/social existants)
if ! grep -q 'prefix="/api/org"' "$MAIN"; then
    sed -i '/social_routes.router, prefix="\/api\/social"/a app.include_router(org_routes.router,    prefix="/api/org",   tags=["OrgHierarchy"])\napp.include_router(org_embed_router,     prefix="/embed/org", tags=["OrgEmbed"])' "$MAIN"
    echo "✓ routers /api/org + /embed/org ajoutés"
else
    echo "= routers déjà présents (skip)"
fi

echo ""
echo "Vérification :"
grep -E "org_hierarchy|org_routes|org_embed_router|/api/org|/embed/org" "$MAIN" || true
echo ""
echo "Restart bizzi-api pour activer :"
echo "  sudo systemctl restart bizzi-api"
