#!/bin/bash
# ================================================================
# Bizzi — Script d'installation automatique
# VPS Ubuntu 22.04 / 24.04
# Usage : sudo bash install.sh
# ================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[BIZZI]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo -e "\n${BLUE}══════════════════════════════════════${NC}\n${BLUE}  $1${NC}\n${BLUE}══════════════════════════════════════${NC}"; }

# ── Vérifications ────────────────────────────────────────────
step "Vérification du système"
[ "$EUID" -ne 0 ] && err "Lance en root : sudo bash install.sh"

RAM_GB=$(free -g | awk '/^Mem:/{print $2}')
DISK_GB=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
log "RAM : ${RAM_GB} Go · Disque libre : ${DISK_GB} Go"
[ "$RAM_GB" -lt 6 ] && warn "Minimum recommandé : 8 Go RAM"
[ "$DISK_GB" -lt 20 ] && err "Espace insuffisant. Minimum : 20 Go libres."

# ── Variables ────────────────────────────────────────────────
BIZZI_DIR="/opt/bizzi"
BIZZI_USER="bizzi"
DB_NAME="bizzi"
DB_USER="bizzi_admin"
DB_PASS=$(openssl rand -hex 16)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_TOKEN=$(openssl rand -hex 24)

# ── Système ──────────────────────────────────────────────────
step "Mise à jour du système"
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git unzip \
    python3 python3-pip python3-venv \
    postgresql postgresql-contrib \
    redis-server nginx supervisor \
    certbot python3-certbot-nginx \
    ufw htop
log "Dépendances installées"

# ── Utilisateur ──────────────────────────────────────────────
step "Création utilisateur bizzi"
id "$BIZZI_USER" &>/dev/null || useradd -m -s /bin/bash "$BIZZI_USER"
log "Utilisateur $BIZZI_USER OK"

# ── PostgreSQL ───────────────────────────────────────────────
step "Configuration PostgreSQL"
systemctl start postgresql && systemctl enable postgresql
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || \
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
log "PostgreSQL : DB=$DB_NAME User=$DB_USER"

# ── Redis ────────────────────────────────────────────────────
step "Configuration Redis"
systemctl start redis-server && systemctl enable redis-server
redis-cli ping | grep -q PONG && log "Redis OK" || warn "Redis ne répond pas"

# ── Ollama + Mistral ─────────────────────────────────────────
step "Installation Ollama + Mistral 7B"
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh
fi
systemctl start ollama 2>/dev/null || ollama serve &>/dev/null &
sleep 5
log "Téléchargement Mistral 7B (5-10 min)..."
ollama pull mistral:7b
log "Mistral 7B OK"

# ── Code Bizzi ───────────────────────────────────────────────
step "Installation Bizzi"
if [ -d "$BIZZI_DIR" ]; then
    cd "$BIZZI_DIR" && git pull
else
    git clone https://github.com/scalap91/bizzi "$BIZZI_DIR"
fi
cd "$BIZZI_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
log "Code Bizzi installé"

# ── .env ─────────────────────────────────────────────────────
step "Création du fichier .env"
cat > "$BIZZI_DIR/.env" << ENV
# Bizzi — Configuration — $(date)

# Serveur
SECRET_KEY=$SECRET_KEY
ENVIRONMENT=production
DEBUG=false

# Base de données
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME

# Redis
REDIS_URL=redis://localhost:6379

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=mistral:7b

# Admin
ADMIN_TOKEN=$ADMIN_TOKEN

# Email (à remplir)
SMTP_HOST=smtp.brevo.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
FROM_EMAIL=contact@bizzi.ai
FROM_NAME=Bizzi

# Domaine
BIZZI_DOMAIN=api.bizzi.ai

# Twilio (optionnel)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_FROM=
ENV
chmod 600 "$BIZZI_DIR/.env"
log ".env créé"

# ── Migration BDD ────────────────────────────────────────────
step "Création des tables"
cd "$BIZZI_DIR"
source venv/bin/activate
python3 -c "
from database.models import Base
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os
load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))
Base.metadata.create_all(engine)
print('Tables créées avec succès')
"
log "Tables BDD créées"

# ── Supervisor ───────────────────────────────────────────────
step "Configuration Supervisor"
mkdir -p /var/log/bizzi
cat > /etc/supervisor/conf.d/bizzi.conf << SUPERVISOR
[program:bizzi-api]
command=$BIZZI_DIR/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 3000 --workers 2
directory=$BIZZI_DIR
user=$BIZZI_USER
autostart=true
autorestart=true
stdout_logfile=/var/log/bizzi/api.log
stderr_logfile=/var/log/bizzi/api.error.log

[program:bizzi-worker]
command=$BIZZI_DIR/venv/bin/celery -A moteur.pipeline worker --loglevel=info
directory=$BIZZI_DIR
user=$BIZZI_USER
autostart=true
autorestart=true
stdout_logfile=/var/log/bizzi/worker.log
stderr_logfile=/var/log/bizzi/worker.error.log
SUPERVISOR

chown -R $BIZZI_USER:$BIZZI_USER /var/log/bizzi "$BIZZI_DIR"
supervisorctl reread && supervisorctl update
supervisorctl start bizzi-api bizzi-worker 2>/dev/null || true
log "Services démarrés"

# ── Nginx ────────────────────────────────────────────────────
step "Configuration Nginx"
cat > /etc/nginx/sites-available/bizzi << NGINX
server {
    listen 80;
    server_name api.bizzi.ai;
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    location /widget.js {
        proxy_pass http://127.0.0.1:3000;
        add_header Access-Control-Allow-Origin *;
        add_header Cache-Control "public, max-age=3600";
    }
}
NGINX
ln -sf /etc/nginx/sites-available/bizzi /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
log "Nginx OK"

# ── Pare-feu ─────────────────────────────────────────────────
step "Pare-feu"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
log "Pare-feu configuré"

# ── Vérification ─────────────────────────────────────────────
step "Vérification finale"
sleep 3
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/status 2>/dev/null || echo "000")
[ "$HTTP" = "200" ] && log "API OK (HTTP $HTTP)" || warn "API HTTP $HTTP — attendre 30s"
pg_isready -U $DB_USER -d $DB_NAME &>/dev/null && log "PostgreSQL OK" || warn "PostgreSQL KO"
redis-cli ping | grep -q PONG && log "Redis OK" || warn "Redis KO"
ollama list | grep -q mistral && log "Ollama + Mistral OK" || warn "Ollama : vérifier"

# ── Résumé ───────────────────────────────────────────────────
IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       BIZZI INSTALLÉ AVEC SUCCÈS          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo "  API          : http://$IP:3000"
echo "  Docs         : http://$IP:3000/docs"
echo ""
echo "  DB Name      : $DB_NAME"
echo "  DB User      : $DB_USER"
echo "  DB Password  : $DB_PASS"
echo ""
echo "  Admin Token  : $ADMIN_TOKEN"
echo ""
echo "  Logs API     : tail -f /var/log/bizzi/api.log"
echo "  Logs Worker  : tail -f /var/log/bizzi/worker.log"
echo ""
echo -e "${YELLOW}  ⚠ Sauvegarde ces infos en lieu sûr !${NC}"
echo -e "${YELLOW}  ⚠ Complète .env avec tes tokens SMTP${NC}"
echo ""
echo "  Prochaine étape :"
echo "  → Pointer api.bizzi.ai vers $IP"
echo "  → certbot --nginx -d api.bizzi.ai"
echo ""
