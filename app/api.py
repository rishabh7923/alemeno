import csv
import io
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db import models
from app.db.database import engine, get_db
from app.tasks.csv_processor import process_csv_data

# Initialize DB tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.get("/")
def home():
    return {"message": "API running"}

@app.post("/jobs/upload")
async def upload_jobs(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")
    
    try:
        # Create a new Job record in the database first to obtain job ID
        db_job = models.Job(
            filename=file.filename,
            status="pending",
            row_count_raw=None
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        # Define upload path
        upload_dir = os.path.join(os.getcwd(), "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, f"job_{db_job.id}_{file.filename}")
        
        # Stream the uploaded file to disk
        bytes_written = 0
        with open(file_path, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                buffer.write(chunk)
                bytes_written += len(chunk)
        
        if bytes_written == 0:
            # Clean up empty file and raise error
            if os.path.exists(file_path):
                os.remove(file_path)
            db_job.status = "failed"
            db_job.error_message = "Uploaded file is empty."
            db.commit()
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            
        # Enqueue the Celery task with the file path
        process_csv_data.delay(db_job.id, file_path)
        
        return {
            "job_id": db_job.id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload CSV file: {str(e)}")

@app.get("/jobs/{job_id}/status")
def get_job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    response = {
        "status": job.status
    }
    
    if job.status == "completed":
        summary_data = None
        if job.summary:
            summary_data = {
                "total_spend_inr": float(job.summary.total_spend_inr) if job.summary.total_spend_inr is not None else 0.0,
                "total_spend_usd": float(job.summary.total_spend_usd) if job.summary.total_spend_usd is not None else 0.0,
                "top_merchants": job.summary.top_merchants or [],
                "anomaly_count": job.summary.anomaly_count or 0,
                "risk_level": job.summary.risk_level
            }
        response["summary"] = summary_data
        
    return response

@app.get("/jobs/{job_id}/results")
def get_job_results(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"Job is not completed. Current status: {job.status}")
        
    cleaned_txns = []
    flagged_anomalies = []
    category_breakdown = {}
    
    for t in job.transactions:
        serialized = {
            "id": t.id,
            "txn_id": t.txn_id,
            "date": t.date,
            "merchant": t.merchant,
            "amount": float(t.amount) if t.amount is not None else None,
            "currency": t.currency,
            "status": t.status,
            "category": t.category,
            "account_id": t.account_id,
            "is_anomaly": t.is_anomaly,
            "anomaly_reason": t.anomaly_reason
        }
        if t.is_anomaly:
            flagged_anomalies.append(serialized)
        else:
            cleaned_txns.append(serialized)
            if t.category and t.amount is not None:
                cat = t.category
                curr = t.currency or "INR"
                amt = float(t.amount)
                if cat not in category_breakdown:
                    category_breakdown[cat] = {}
                category_breakdown[cat][curr] = category_breakdown[cat].get(curr, 0.0) + amt
    
    narrative = job.summary.narrative if job.summary else ""
    
    return {
        "transactions": cleaned_txns,
        "anomalies": flagged_anomalies,
        "spending": category_breakdown,
        "summary": narrative
    }

@app.get("/jobs")
def get_jobs(status: str = None, db: Session = Depends(get_db)):
    query = db.query(models.Job)
    if status:
        query = query.filter(models.Job.status == status)
    jobs = query.all()
    
    return [
        {
            "id": job.id,
            "status": job.status,
            "filename": job.filename,
            "row_count": job.row_count_raw,
            "created_at": job.created_at
        }
        for job in jobs
    ]
