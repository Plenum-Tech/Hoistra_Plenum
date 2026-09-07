"""ORM models for the dynamic approval engine."""
from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from .base import Base


class ApprovalRule(Base):
    __tablename__ = "wo_approval_rules"
    __table_args__ = {"schema": "plenum_cafm"}

    rule_id = Column(Integer, primary_key=True, autoincrement=True)
    dimension = Column(String(20), nullable=False)
    match_value = Column(String(50))
    match_operator = Column(String(10))
    match_threshold = Column(Numeric)
    match_threshold_upper = Column(Numeric)
    weight = Column(Integer, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ApprovalThreshold(Base):
    __tablename__ = "wo_approval_thresholds"
    __table_args__ = {"schema": "plenum_cafm"}

    level = Column(Integer, primary_key=True)
    min_score = Column(Integer, nullable=False)
    max_score = Column(Integer)
    required_roles = Column(ARRAY(Text))


class ApprovalSuggestion(Base):
    __tablename__ = "wo_approval_suggestions"
    __table_args__ = {"schema": "plenum_cafm"}

    suggestion_id = Column(Integer, primary_key=True, autoincrement=True)
    work_order_id = Column(String(50), nullable=False)
    fingerprint = Column(String(64))
    source = Column(String(20))
    confidence = Column(String(10))
    match_score = Column(Integer)
    risk_score = Column(Integer)
    suggested_chain = Column(JSONB)
    accepted = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
