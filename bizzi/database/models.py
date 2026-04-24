"""
database/models.py
===================
Modèles de base de données Bizzi.

Tables :
  - tenants          : les clients
  - agents           : les agents de chaque client
  - regions          : régions géographiques
  - cities           : villes
  - categories       : catégories éditoriales
  - sources          : sources RSS/API par client
  - events           : événements (déduplication)
  - productions      : chaque contenu généré
  - article_sources  : sources liées à un événement/article
  - qa_scores        : scores qualité par production
  - editorial_reviews: décisions du rédacteur en chef
  - internal_links   : maillage interne
  - pipeline_runs    : historique des runs pipeline
  - ads_rules        : règles publicitaires
  - article_scores   : scoring automatique
  - article_images   : hash images déduplication
  - publication_limits: règles de volume
  - publication_queue : file de publication
  - publication_logs  : historique décisions
  - editorial_decisions: décisions rédacteur en chef IA
"""

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, Enum, Index, JSON
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()


# ── TENANTS ───────────────────────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id            = Column(Integer, primary_key=True)
    slug          = Column(String(50),  unique=True, nullable=False)
    name          = Column(String(100), nullable=False)
    domain        = Column(String(50),  nullable=False)   # media, politics, diagnostic...
    plan          = Column(String(20),  nullable=False)   # starter, pro, business, enterprise
    token_hash    = Column(String(200), nullable=False)
    yaml_config   = Column(Text,        nullable=True)    # config YAML du client
    color         = Column(String(10),  default="#e02d2d")
    site_url      = Column(String(200))
    contact_email = Column(String(200))
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())
    updated_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relations
    agents         = relationship("Agent",         back_populates="tenant", cascade="all, delete")
    productions    = relationship("Production",    back_populates="tenant", cascade="all, delete")
    pipeline_runs  = relationship("PipelineRun",   back_populates="tenant", cascade="all, delete")
    regions        = relationship("Region",        back_populates="tenant", cascade="all, delete")
    cities         = relationship("City",          back_populates="tenant", cascade="all, delete")
    categories     = relationship("Category",      back_populates="tenant", cascade="all, delete")
    sources        = relationship("Source",        back_populates="tenant", cascade="all, delete")
    events         = relationship("Event",         back_populates="tenant", cascade="all, delete")

    def __repr__(self):
        return f"<Tenant {self.slug} · {self.plan}>"


# ── AGENTS ────────────────────────────────────────────────────

class AgentStatus(str, enum.Enum):
    active  = "active"
    paused  = "paused"
    error   = "error"
    idle    = "idle"

class Agent(Base):
    __tablename__ = "agents"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    slug                = Column(String(100), nullable=False)
    name                = Column(String(100), nullable=False)
    role                = Column(String(50),  nullable=False)  # journaliste, redacteur_en_chef, analyste
    agent_id            = Column(String(50),  nullable=False)
    personality         = Column(Text,        nullable=True)
    system_prompt       = Column(Text,        nullable=True)   # prompt injecté dans GPT
    tone                = Column(String(50),  nullable=True)   # neutre, incisif, pédagogique
    style               = Column(String(50),  nullable=True)   # clair, analytique, storytelling
    specialty           = Column(String(100), nullable=True)   # économie, local, sport...
    aggressiveness      = Column(Integer,     default=2)
    verification_level  = Column(String(20),  default="strict")
    local_focus         = Column(String(20),  default="moyen")
    memory_enabled      = Column(Boolean,     default=True)
    memory_summary      = Column(Text,        nullable=True)
    color               = Column(String(10),  default="#374151")
    status              = Column(Enum(AgentStatus), default=AgentStatus.active)
    last_active         = Column(DateTime,    nullable=True)
    productions_count   = Column(Integer,     default=0)
    avg_score           = Column(Float,       default=0.0)
    created_at          = Column(DateTime,    server_default=func.now())
    updated_at          = Column(DateTime,    server_default=func.now(), onupdate=func.now())

    # Relations
    tenant      = relationship("Tenant",     back_populates="agents")
    productions = relationship("Production", back_populates="agent")

    __table_args__ = (
        Index("idx_agent_tenant", "tenant_id"),
        Index("idx_agent_slug",   "tenant_id", "slug"),
    )


# ── REGIONS ───────────────────────────────────────────────────

class Region(Base):
    __tablename__ = "regions"

    id              = Column(Integer, primary_key=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name            = Column(String(200), nullable=False)
    slug            = Column(String(200), nullable=False)
    priority_score  = Column(Integer, default=50)
    active          = Column(Boolean, default=True)
    created_at      = Column(DateTime, server_default=func.now())

    # Relations
    tenant      = relationship("Tenant", back_populates="regions")
    cities      = relationship("City",   back_populates="region")
    events      = relationship("Event",  back_populates="region")

    __table_args__ = (
        Index("idx_region_tenant", "tenant_id"),
    )


# ── CITIES ────────────────────────────────────────────────────

class City(Base):
    __tablename__ = "cities"

    id              = Column(Integer, primary_key=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    region_id       = Column(Integer, ForeignKey("regions.id"), nullable=True)
    name            = Column(String(200), nullable=False)
    slug            = Column(String(200), nullable=False)
    department      = Column(String(100), nullable=True)
    population      = Column(Integer,     nullable=True)
    priority_score  = Column(Integer,     default=50)
    active          = Column(Boolean,     default=True)
    created_at      = Column(DateTime,    server_default=func.now())

    # Relations
    tenant  = relationship("Tenant", back_populates="cities")
    region  = relationship("Region", back_populates="cities")
    events  = relationship("Event",  back_populates="city")

    __table_args__ = (
        Index("idx_city_tenant", "tenant_id"),
        Index("idx_city_region", "region_id"),
    )


# ── CATEGORIES ────────────────────────────────────────────────

class Category(Base):
    __tablename__ = "categories"

    id          = Column(Integer, primary_key=True)
    tenant_id   = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name        = Column(String(200), nullable=False)
    slug        = Column(String(200), nullable=False)
    parent_id   = Column(Integer,     nullable=True)
    active      = Column(Boolean,     default=True)
    created_at  = Column(DateTime,    server_default=func.now())

    # Relations
    tenant = relationship("Tenant", back_populates="categories")

    __table_args__ = (
        Index("idx_category_tenant", "tenant_id"),
    )


# ── SOURCES ───────────────────────────────────────────────────

class Source(Base):
    __tablename__ = "sources"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    category_id         = Column(Integer, ForeignKey("categories.id"), nullable=True)
    region_id           = Column(Integer, ForeignKey("regions.id"),    nullable=True)
    name                = Column(String(200), nullable=False)
    url                 = Column(Text,        nullable=False)
    type                = Column(String(50),  default="rss")  # rss, api, scraping, telephone
    reliability_score   = Column(Integer,     default=70)
    last_checked_at     = Column(DateTime,    nullable=True)
    active              = Column(Boolean,     default=True)
    created_at          = Column(DateTime,    server_default=func.now())

    # Relations
    tenant   = relationship("Tenant", back_populates="sources")

    __table_args__ = (
        Index("idx_source_tenant", "tenant_id"),
    )


# ── EVENTS ────────────────────────────────────────────────────
# 1 événement = 1 article principal
# Plusieurs sources = enrichissement du même article

class EventStatus(str, enum.Enum):
    open    = "open"
    covered = "covered"
    closed  = "closed"

class Event(Base):
    __tablename__ = "events"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    city_id             = Column(Integer, ForeignKey("cities.id"),   nullable=True)
    region_id           = Column(Integer, ForeignKey("regions.id"),  nullable=True)
    title               = Column(String(500), nullable=False)
    event_type          = Column(String(100), nullable=True)
    occurred_at         = Column(DateTime,    nullable=True)
    first_source_url    = Column(Text,        nullable=True)
    image_hash          = Column(String(64),  nullable=True)   # pHash image principale
    dedup_score         = Column(Integer,     default=0)       # score déduplication global
    entities            = Column(JSON,        nullable=True)   # personnes/organisations citées
    keywords            = Column(JSON,        nullable=True)   # mots-clés extraits
    status              = Column(Enum(EventStatus), default=EventStatus.open)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relations
    tenant      = relationship("Tenant",      back_populates="events")
    city        = relationship("City",        back_populates="events")
    region      = relationship("Region",      back_populates="events")
    productions = relationship("Production",  back_populates="event")
    sources     = relationship("ArticleSource", back_populates="event")

    __table_args__ = (
        Index("idx_event_tenant",     "tenant_id"),
        Index("idx_event_region",     "region_id"),
        Index("idx_event_city",       "city_id"),
        Index("idx_event_image_hash", "image_hash"),
    )


# ── PRODUCTIONS ───────────────────────────────────────────────

class ProductionStatus(str, enum.Enum):
    generated           = "generated"
    draft               = "draft"
    scored              = "scored"
    editor_review       = "editor_review"
    approved_by_editor  = "approved_by_editor"
    rewrite_requested   = "rewrite_requested"
    scheduled           = "scheduled"
    published           = "published"
    rejected            = "rejected"
    archived            = "archived"
    duplicate           = "duplicate"
    enrich_existing     = "enrich_existing"

class Production(Base):
    __tablename__ = "productions"

    id                      = Column(Integer, primary_key=True)
    tenant_id               = Column(Integer, ForeignKey("tenants.id"),      nullable=False)
    agent_id                = Column(Integer, ForeignKey("agents.id"),        nullable=True)
    pipeline_run_id         = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    event_id                = Column(Integer, ForeignKey("events.id"),        nullable=True)
    category_id             = Column(Integer, ForeignKey("categories.id"),    nullable=True)
    city_id                 = Column(Integer, ForeignKey("cities.id"),        nullable=True)
    region_id               = Column(Integer, ForeignKey("regions.id"),       nullable=True)
    editor_agent_id         = Column(Integer, ForeignKey("agents.id"),        nullable=True)

    # Contenu
    content_type            = Column(String(50),  default="article")
    title                   = Column(String(500), nullable=True)
    slug                    = Column(String(300), nullable=True, unique=True)
    excerpt                 = Column(Text,        nullable=True)
    content_html            = Column(Text,        nullable=True)
    content_raw             = Column(Text,        nullable=True)
    image_url               = Column(Text,        nullable=True)
    image_alt               = Column(String(300), nullable=True)
    word_count              = Column(Integer,     default=0)

    # SEO
    meta_title              = Column(String(200), nullable=True)
    meta_description        = Column(String(300), nullable=True)
    canonical_url           = Column(Text,        nullable=True)
    schema_type             = Column(String(50),  default="NewsArticle")

    # Scores qualité
    quality_score           = Column(Integer, default=0)
    similarity_score        = Column(Float,   default=0.0)
    seo_score               = Column(Integer, default=0)
    local_value_score       = Column(Integer, default=0)
    readability_score       = Column(Integer, default=0)
    duplicate_score         = Column(Float,   default=0.0)
    source_reliability_score= Column(Integer, default=0)

    # Validation
    fact_check_status       = Column(String(30),  default="pending")
    status                  = Column(Enum(ProductionStatus), default=ProductionStatus.draft)

    # QA legacy
    qa_score                = Column(Float,   nullable=True)
    qa_passed               = Column(Boolean, nullable=True)
    qa_reason               = Column(Text,    nullable=True)
    trash_reason            = Column(Text,    nullable=True)
    trashed_at              = Column(DateTime, nullable=True)
    published_at            = Column(DateTime, nullable=True)
    created_at              = Column(DateTime, server_default=func.now())
    updated_at              = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relations
    tenant          = relationship("Tenant",      back_populates="productions")
    agent           = relationship("Agent",       back_populates="productions", foreign_keys=[agent_id])
    pipeline_run    = relationship("PipelineRun", back_populates="productions")
    event           = relationship("Event",       back_populates="productions")
    article_sources = relationship("ArticleSource", back_populates="production")
    internal_links  = relationship("InternalLink",  back_populates="production",  foreign_keys="InternalLink.production_id")
    reviews         = relationship("EditorialReview", back_populates="production")

    __table_args__ = (
        Index("idx_prod_tenant",   "tenant_id"),
        Index("idx_prod_status",   "tenant_id", "status"),
        Index("idx_prod_slug",     "slug"),
        Index("idx_prod_region",   "region_id"),
        Index("idx_prod_city",     "city_id"),
        Index("idx_prod_event",    "event_id"),
        Index("idx_prod_created",  "tenant_id", "created_at"),
    )


# ── ARTICLE SOURCES ───────────────────────────────────────────

class ArticleSource(Base):
    __tablename__ = "article_sources"

    id                  = Column(Integer, primary_key=True)
    event_id            = Column(Integer, ForeignKey("events.id"),      nullable=True)
    production_id       = Column(Integer, ForeignKey("productions.id"), nullable=True)
    source_url          = Column(Text,        nullable=False)
    source_name         = Column(String(200), nullable=True)
    source_title        = Column(Text,        nullable=True)
    source_content      = Column(Text,        nullable=True)
    source_published_at = Column(DateTime,    nullable=True)
    image_url           = Column(Text,        nullable=True)
    image_hash          = Column(String(64),  nullable=True)  # pHash pour comparaison
    collected_at        = Column(DateTime,    server_default=func.now())

    # Relations
    event       = relationship("Event",      back_populates="sources")
    production  = relationship("Production", back_populates="article_sources")

    __table_args__ = (
        Index("idx_asource_event",      "event_id"),
        Index("idx_asource_production", "production_id"),
    )


# ── EDITORIAL REVIEWS ─────────────────────────────────────────

class EditorialReview(Base):
    __tablename__ = "editorial_reviews"

    id                  = Column(Integer, primary_key=True)
    production_id       = Column(Integer, ForeignKey("productions.id"), nullable=False)
    reviewer_agent_id   = Column(Integer, ForeignKey("agents.id"),      nullable=True)
    decision            = Column(String(30), nullable=False)  # published, rejected, needs_human_check
    comments            = Column(Text,    nullable=True)
    scores              = Column(JSON,    nullable=True)
    created_at          = Column(DateTime, server_default=func.now())

    # Relations
    production = relationship("Production", back_populates="reviews")

    __table_args__ = (
        Index("idx_review_production", "production_id"),
    )


# ── INTERNAL LINKS ────────────────────────────────────────────

class InternalLink(Base):
    __tablename__ = "internal_links"

    id                  = Column(Integer, primary_key=True)
    production_id       = Column(Integer, ForeignKey("productions.id"), nullable=False)
    linked_production_id= Column(Integer, ForeignKey("productions.id"), nullable=False)
    link_type           = Column(String(50), nullable=True)  # related, region, city, category
    created_at          = Column(DateTime, server_default=func.now())

    # Relations
    production = relationship("Production", back_populates="internal_links", foreign_keys=[production_id])

    __table_args__ = (
        Index("idx_ilink_production", "production_id"),
    )


# ── PIPELINE RUNS ─────────────────────────────────────────────

class PipelineStatus(str, enum.Enum):
    running   = "running"
    completed = "completed"
    failed    = "failed"
    scheduled = "scheduled"

class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    status              = Column(Enum(PipelineStatus), default=PipelineStatus.scheduled)
    current_step        = Column(String(50),  nullable=True)
    total_steps         = Column(Integer,     default=12)
    step_number         = Column(Integer,     default=0)
    duration_sec        = Column(Float,       nullable=True)
    productions_count   = Column(Integer,     default=0)
    avg_score           = Column(Float,       nullable=True)
    error_msg           = Column(Text,        nullable=True)
    started_at          = Column(DateTime,    nullable=True)
    completed_at        = Column(DateTime,    nullable=True)
    scheduled_at        = Column(DateTime,    server_default=func.now())

    # Relations
    tenant      = relationship("Tenant",     back_populates="pipeline_runs")
    productions = relationship("Production", back_populates="pipeline_run")

    __table_args__ = (
        Index("idx_run_tenant",  "tenant_id"),
        Index("idx_run_status",  "tenant_id", "status"),
        Index("idx_run_created", "tenant_id", "scheduled_at"),
    )


# ── ADS RULES ─────────────────────────────────────────────────

class AdsRule(Base):
    __tablename__ = "ads_rules"

    id          = Column(Integer, primary_key=True)
    tenant_id   = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    page_type   = Column(String(50),  nullable=False)   # article, region, city, home
    ad_position = Column(String(50),  nullable=False)   # header, in_article, sidebar, footer
    active      = Column(Boolean,     default=True)
    conditions  = Column(JSON,        nullable=True)
    created_at  = Column(DateTime,    server_default=func.now())

    __table_args__ = (
        Index("idx_ads_tenant", "tenant_id"),
    )
# ── NOUVELLES TABLES — FILTRE ÉDITORIAL ─────────────────────


# ── ARTICLE SCORES ────────────────────────────────────────────
# Scoring automatique — aide à la décision du rédacteur en chef

class ArticleScore(Base):
    __tablename__ = "article_scores"

    id                      = Column(Integer, primary_key=True)
    tenant_id               = Column(Integer, ForeignKey("tenants.id"),     nullable=False)
    production_id           = Column(Integer, ForeignKey("productions.id"), nullable=False)

    # 5 critères principaux /20 chacun
    news_value_score        = Column(Integer, default=0)   # intérêt journalistique
    freshness_score         = Column(Integer, default=0)   # fraîcheur
    user_value_score        = Column(Integer, default=0)   # utilité lecteur
    originality_score       = Column(Integer, default=0)   # originalité
    seo_potential_score     = Column(Integer, default=0)   # potentiel SEO

    # Score global
    publication_score       = Column(Integer, default=0)   # somme des 5 /100

    # Scores qualité
    quality_score           = Column(Integer, default=0)
    similarity_score        = Column(Float,   default=0.0)
    local_value_score       = Column(Integer, default=0)
    readability_score       = Column(Integer, default=0)
    source_reliability_score= Column(Integer, default=0)

    # Scores déduplication
    duplicate_risk_score    = Column(Integer, default=0)   # risque doublon /100
    image_similarity_score  = Column(Float,   default=0.0) # similarité image

    # Décision automatique
    final_decision          = Column(String(30),  nullable=True)
    decision_reason         = Column(Text,        nullable=True)
    scored_at               = Column(DateTime,    server_default=func.now())

    __table_args__ = (
        Index("idx_ascore_tenant",      "tenant_id"),
        Index("idx_ascore_production",  "production_id"),
        Index("idx_ascore_pub_score",   "tenant_id", "publication_score"),
    )


# ── ARTICLE IMAGES ────────────────────────────────────────────
# Hash images pour déduplication événementielle

class ArticleImage(Base):
    __tablename__ = "article_images"

    id              = Column(Integer, primary_key=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"),     nullable=False)
    production_id   = Column(Integer, ForeignKey("productions.id"), nullable=True)
    event_id        = Column(Integer, ForeignKey("events.id"),       nullable=True)
    image_url       = Column(Text,        nullable=True)
    image_hash      = Column(String(64),  nullable=True)  # hash MD5
    image_phash     = Column(String(64),  nullable=True)  # perceptual hash
    image_dhash     = Column(String(64),  nullable=True)  # difference hash
    image_width     = Column(Integer,     nullable=True)
    image_height    = Column(Integer,     nullable=True)
    source_url      = Column(Text,        nullable=True)
    created_at      = Column(DateTime,    server_default=func.now())

    __table_args__ = (
        Index("idx_aimg_tenant",     "tenant_id"),
        Index("idx_aimg_production", "production_id"),
        Index("idx_aimg_phash",      "image_phash"),
    )


# ── PUBLICATION LIMITS ────────────────────────────────────────
# Règles de volume par client

class PublicationLimit(Base):
    __tablename__ = "publication_limits"

    id                              = Column(Integer, primary_key=True)
    tenant_id                       = Column(Integer, ForeignKey("tenants.id"), nullable=False, unique=True)
    max_articles_per_day            = Column(Integer, default=30)
    max_articles_per_hour           = Column(Integer, default=3)
    max_articles_per_category_per_day = Column(Integer, default=8)
    max_articles_per_city_per_day   = Column(Integer, default=3)
    max_articles_per_region_per_day = Column(Integer, default=10)
    active                          = Column(Boolean, default=True)
    created_at                      = Column(DateTime, server_default=func.now())
    updated_at                      = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_publimit_tenant", "tenant_id"),
    )


# ── PUBLICATION QUEUE ─────────────────────────────────────────
# File de publication priorisée

class QueueStatus(str, enum.Enum):
    pending     = "pending"
    processing  = "processing"
    published   = "published"
    cancelled   = "cancelled"

class PublicationQueue(Base):
    __tablename__ = "publication_queue"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"),     nullable=False)
    production_id       = Column(Integer, ForeignKey("productions.id"), nullable=False)
    publication_score   = Column(Integer, default=0)
    priority            = Column(Integer, default=5)       # 1=urgent, 10=faible
    scheduled_for       = Column(DateTime, nullable=True)
    status              = Column(Enum(QueueStatus), default=QueueStatus.pending)
    reason              = Column(Text,    nullable=True)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_queue_tenant",   "tenant_id"),
        Index("idx_queue_status",   "tenant_id", "status"),
        Index("idx_queue_priority", "tenant_id", "priority", "publication_score"),
    )


# ── PUBLICATION LOGS ──────────────────────────────────────────
# Historique complet des décisions

class PublicationLog(Base):
    __tablename__ = "publication_logs"

    id              = Column(Integer, primary_key=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"),     nullable=False)
    production_id   = Column(Integer, ForeignKey("productions.id"), nullable=False)
    action          = Column(String(50),  nullable=False)  # published, rejected, archived...
    old_status      = Column(String(50),  nullable=True)
    new_status      = Column(String(50),  nullable=True)
    reason          = Column(Text,        nullable=True)
    actor_type      = Column(String(20),  nullable=True)   # agent, system, human
    actor_id        = Column(Integer,     nullable=True)
    created_at      = Column(DateTime,    server_default=func.now())

    __table_args__ = (
        Index("idx_publog_tenant",     "tenant_id"),
        Index("idx_publog_production", "production_id"),
        Index("idx_publog_action",     "tenant_id", "action"),
    )


# ── EDITORIAL DECISIONS ───────────────────────────────────────
# Décisions du rédacteur en chef IA

class EditorialDecision(Base):
    __tablename__ = "editorial_decisions"

    id                  = Column(Integer, primary_key=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"),     nullable=False)
    production_id       = Column(Integer, ForeignKey("productions.id"), nullable=False)
    editor_agent_id     = Column(Integer, ForeignKey("agents.id"),      nullable=True)

    # Décision
    decision            = Column(String(30),  nullable=False)
    # published, rejected, archived, rewrite_requested,
    # duplicate, enrich_existing, scheduled, needs_review

    decision_reason     = Column(Text,        nullable=True)
    priority_level      = Column(Integer,     default=5)   # 1=urgent, 10=faible
    requested_changes   = Column(Text,        nullable=True)  # si réécriture demandée
    editor_notes        = Column(Text,        nullable=True)  # notes internes
    scores_snapshot     = Column(JSON,        nullable=True)  # snapshot des scores au moment de la décision
    created_at          = Column(DateTime,    server_default=func.now())

    __table_args__ = (
        Index("idx_editdec_tenant",     "tenant_id"),
        Index("idx_editdec_production", "production_id"),
        Index("idx_editdec_decision",   "tenant_id", "decision"),
    )
