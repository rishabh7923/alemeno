import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean, Numeric, JSON
from sqlalchemy.orm import relationship
from app.db.database import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)
    row_count_raw = Column(Integer, nullable=True)
    row_count_clean = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", uselist=False, back_populates="job", cascade="all, delete-orphan")

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    txn_id = Column(String, nullable=True)
    date = Column(String, nullable=True)
    merchant = Column(String, nullable=True)
    amount = Column(Numeric(10, 2), nullable=True)
    currency = Column(String, nullable=True)
    status = Column(String, nullable=True)
    category = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    
    is_anomaly = Column(Boolean, default=False, nullable=False)
    anomaly_reason = Column(Text, nullable=True)
    
    llm_category = Column(String, nullable=True)
    llm_raw_response = Column(Text, nullable=True)
    llm_failed = Column(Boolean, default=False, nullable=False)

    job = relationship("Job", back_populates="transactions")

class JobSummary(Base):
    __tablename__ = "job_summaries"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    total_spend_inr = Column(Numeric(12, 2), nullable=True)
    total_spend_usd = Column(Numeric(12, 2), nullable=True)
    top_merchants = Column(JSON, nullable=True)
    anomaly_count = Column(Integer, nullable=True)
    narrative = Column(Text, nullable=True)
    risk_level = Column(String, nullable=True)

    job = relationship("Job", back_populates="summary")
