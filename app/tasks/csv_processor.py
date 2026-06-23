import datetime
import time
import os
import re
import statistics
import json
import csv

from app.db.database import SessionLocal
from app.db import models
from app.core.celery import app
from app.services.gemini import (
    gemini,
    get_classification_prompt,
    get_summary_prompt,
    API_EXCEPTIONS
)

def remove_duplicates(records: list[dict]) -> list[dict]:
    """Remove exact duplicate rows from raw records."""
    seen = set()
    unique_records = []
    for record in records:
        # Convert dictionary keys and values to strings to create a hashable tuple
        record_tuple = tuple(sorted((k, str(v).strip() if v is not None else "") for k, v in record.items()))
        if record_tuple not in seen:
            seen.add(record_tuple)
            unique_records.append(record)
    return unique_records


def calculate_account_medians(records: list[dict]) -> dict[str, float]:
    """Group amounts by account ID to calculate medians efficiently."""
    account_medians = {}
    account_amounts = {}
    
    for record in records:
        acct_val = record.get("account_id")
        if acct_val is not None:
            acct_id = str(acct_val).strip()
            if acct_id not in account_medians:
                account_medians[acct_id] = 0.0
                account_amounts[acct_id] = []
            
            amt_str = record.get("amount")
            if amt_str is not None and str(amt_str).strip() != "":
                try:
                    clean_amt = re.sub(r'[^\d.-]', '', str(amt_str).strip())
                    account_amounts[acct_id].append(float(clean_amt))
                except ValueError:
                    pass

    for acct_id, amounts in account_amounts.items():
        if amounts:
            account_medians[acct_id] = statistics.median(amounts)
            
    return account_medians


def clean_and_detect_anomalies(
    records: list[dict], 
    account_medians: dict[str, float], 
    job_id: int
) -> tuple[list[models.Transaction], int]:
    """Perform normalization, anomaly detection rules, and instantiate models.Transaction objects."""
    clean_count = 0
    inserted_transactions = []
    
    for record in records:
        txn_id = record.get("txn_id")
        date_val = record.get("date")
        merchant = record.get("merchant")
        amount_str = record.get("amount")
        currency = record.get("currency")
        status = record.get("status")
        category = record.get("category")
        account_id = record.get("account_id")
        
        # Normalize fields
        clean_txn_id = str(txn_id).strip() if txn_id is not None else None
        clean_merchant = str(merchant).strip() if merchant is not None else None
        clean_currency = str(currency).strip() if currency is not None else None
        clean_account_id = str(account_id).strip() if account_id is not None else None
        
        # Normalize Date to ISO 8601
        normalized_date = None
        date_parse_success = False
        if date_val is not None and str(date_val).strip() != "":
            try:
                from dateutil import parser as date_parser
                parsed_dt = date_parser.parse(str(date_val).strip())
                normalized_date = parsed_dt.strftime("%Y-%m-%d")
                date_parse_success = True
            except Exception:
                normalized_date = str(date_val).strip()
        
        # Clean Amount
        amount = None
        amount_parse_success = False
        if amount_str is not None and str(amount_str).strip() != "":
            try:
                clean_amt = re.sub(r'[^\d.-]', '', str(amount_str).strip())
                amount = float(clean_amt)
                amount_parse_success = True
            except ValueError:
                pass
        
        # Uppercase Status
        normalized_status = str(status).strip().upper() if status is not None else None
        
        # Fill missing category with 'Uncategorised'
        normalized_category = str(category).strip() if category is not None and str(category).strip() != "" else "Uncategorised"
        
        # Run Anomaly Detection
        is_anomaly = False
        reasons = []
        
        if not clean_txn_id:
            is_anomaly = True
            reasons.append("Missing transaction ID")
        if not date_val or str(date_val).strip() == "":
            is_anomaly = True
            reasons.append("Missing date")
        elif not date_parse_success:
            is_anomaly = True
            reasons.append(f"Invalid date format: {date_val}")
            
        if not clean_merchant:
            is_anomaly = True
            reasons.append("Missing merchant")
            
        if amount_str is None or str(amount_str).strip() == "":
            is_anomaly = True
            reasons.append("Missing amount")
        elif not amount_parse_success:
            is_anomaly = True
            reasons.append(f"Invalid amount format: {amount_str}")
        elif amount <= 0:
            is_anomaly = True
            reasons.append("Non-positive amount")
            
        # Exceeds 3x account median
        if amount_parse_success and amount > 0 and clean_account_id:
            median = account_medians.get(clean_account_id, 0.0)
            if median > 0 and amount > 3 * median:
                is_anomaly = True
                reasons.append(f"Amount {amount} exceeds 3x the account's median ({median})")
                
        # USD but domestic brand
        if clean_currency == "USD" and clean_merchant:
            merchant_lower = clean_merchant.lower()
            domestic_brands = ["swiggy", "ola", "irctc"]
            if any(brand in merchant_lower for brand in domestic_brands):
                is_anomaly = True
                reasons.append("USD transaction for domestic-only brand")
                
        anomaly_reason = "; ".join(reasons) if is_anomaly else None
        if not is_anomaly:
            clean_count += 1
            
        # Create transaction
        txn = models.Transaction(
            job_id=job_id,
            txn_id=clean_txn_id,
            date=normalized_date,
            merchant=clean_merchant,
            amount=amount,
            currency=clean_currency,
            status=normalized_status,
            category=normalized_category,
            account_id=clean_account_id,
            is_anomaly=is_anomaly,
            anomaly_reason=anomaly_reason,
            llm_failed=False
        )
        inserted_transactions.append(txn)
        
    return inserted_transactions, clean_count


def classify_transactions_with_llm(uncategorised_txns: list[models.Transaction]) -> None:
    """Classify uncategorised transactions in batches using Gemini API."""
    if not uncategorised_txns:
        return
        
    print(f"Classifying {len(uncategorised_txns)} uncategorised transactions in batches using Gemini API...")
    batch_size = 20
    
    for i in range(0, len(uncategorised_txns), batch_size):
        batch = uncategorised_txns[i : i + batch_size]
        records_to_classify = []
        for idx, t in enumerate(batch):
            records_to_classify.append({
                "idx": str(idx),
                "merchant": t.merchant or "Unknown",
                "amount": float(t.amount) if t.amount is not None else 0.0,
                "currency": t.currency or "INR"
            })
        
        try:
            prompt = get_classification_prompt(records_to_classify)
            response_text = gemini(prompt, json_mode=True)
            classified_map = json.loads(response_text)
            
            for idx, t in enumerate(batch):
                cat_val = classified_map.get(str(idx))
                # Fallback if invalid category returned
                allowed_categories = ["Food", "Shopping", "Travel", "Transport", "Utilities", "Cash Withdrawal", "Entertainment", "Other"]
                if cat_val not in allowed_categories:
                    cat_val = "Other"
                t.category = cat_val
                t.llm_category = cat_val
                t.llm_raw_response = response_text
                t.llm_failed = False
        except API_EXCEPTIONS as e:
            print(f"Gemini API batch classification {i} failed after retries: {e}")
            # Mark batch as failed, do not fail job
            for t in batch:
                t.llm_failed = True
                t.llm_category = None
                t.llm_raw_response = str(e)


def calculate_transaction_stats(all_txns: list[models.Transaction]) -> dict:
    """Calculate statistics from transactions."""
    from collections import Counter
    total_anomaly_count = 0
    total_spend_inr = 0.0
    total_spend_usd = 0.0
    
    merchant_counts = Counter()
    category_breakdown = {}
    anomaly_reasons_summary = {}
    
    for t in all_txns:
        if t.is_anomaly:
            total_anomaly_count += 1
            if t.anomaly_reason:
                # Simplify reason for summary
                reason = t.anomaly_reason
                if "exceeds 3x" in reason:
                    reason_key = "Transaction amount exceeds 3x account median"
                else:
                    reason_key = reason
                anomaly_reasons_summary[reason_key] = anomaly_reasons_summary.get(reason_key, 0) + 1
                
        if t.amount is not None:
            amt_val = float(t.amount)
            if t.currency == "INR":
                total_spend_inr += amt_val
            elif t.currency == "USD":
                total_spend_usd += amt_val
                
        if t.merchant:
            merchant_counts[t.merchant] += 1
            
        if t.category and t.amount is not None:
            cat = t.category
            curr = t.currency or "INR"
            amt = float(t.amount)
            if cat not in category_breakdown:
                category_breakdown[cat] = {}
            category_breakdown[cat][curr] = round(category_breakdown[cat].get(curr, 0.0) + amt, 2)

    # Get top 3 merchants by transaction frequency
    top_3_merchants = [m for m, count in merchant_counts.most_common(3)]
    
    # Round the total spends
    total_spend_inr = round(total_spend_inr, 2)
    total_spend_usd = round(total_spend_usd, 2)
    
    return {
        "total_transactions": len(all_txns),
        "total_spend_by_currency": {
            "INR": total_spend_inr,
            "USD": total_spend_usd
        },
        "category_breakdown": category_breakdown,
        "anomaly_count": total_anomaly_count,
        "top_3_merchants": top_3_merchants,
        "anomaly_reasons_summary": anomaly_reasons_summary
    }


def generate_narrative_summary(stats: dict, job_id: int) -> models.JobSummary:
    """Generate narrative summary and create JobSummary model."""
    try:
        summary_prompt = get_summary_prompt(stats)
        response_text = gemini(summary_prompt, json_mode=True)
        summary_dict = json.loads(response_text)
    except API_EXCEPTIONS as e:
        print(f"Gemini API narrative summary failed after retries: {e}")
        summary_dict = {
            "spending_narrative": f"Failed to generate summary: {e}",
            "risk_level": "medium"
        }
        
    return models.JobSummary(
        job_id=job_id,
        total_spend_inr=stats["total_spend_by_currency"]["INR"],
        total_spend_usd=stats["total_spend_by_currency"]["USD"],
        top_merchants=stats["top_3_merchants"],
        anomaly_count=stats["anomaly_count"],
        narrative=summary_dict.get("spending_narrative", ""),
        risk_level=summary_dict.get("risk_level", "low")
    )


@app.task
def process_csv_data(job_id: int, file_path: str):
    db = SessionLocal()
    try:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        if not job:
            print(f"Job with ID {job_id} not found in database.")
            return f"Job {job_id} not found"
            
        job.status = "processing"
        db.commit()
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"CSV file not found at: {file_path}")
            
        records = []
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                records.append(row)
                
        if not records:
            raise ValueError("CSV file is empty or has no data rows.")
            
        print(f"Processing {len(records)} records for Job {job_id}...")
        
        # 1. Remove exact duplicate rows from raw records
        unique_records = remove_duplicates(records)
        print(f"Removed {len(records) - len(unique_records)} exact duplicate rows. Unique rows: {len(unique_records)}")
        
        # 2. Calculate medians for each account based on the current batch records only
        account_medians = calculate_account_medians(unique_records)
                
        # 3. Data Cleaning & Anomaly Detection Pass
        inserted_transactions, clean_count = clean_and_detect_anomalies(unique_records, account_medians, job_id)
        for txn in inserted_transactions: db.add(txn)
            
        db.commit()
        
        # 4. LLM Classification
        uncategorised_txns = [t for t in inserted_transactions if t.category == "Uncategorised"]
        classify_transactions_with_llm(uncategorised_txns)
        db.commit()
            
        # 5. LLM Narrative Summary
        # Re-fetch all processed transactions for the job to build summary
        all_txns = db.query(models.Transaction).filter(models.Transaction.job_id == job_id).all()
        stats = calculate_transaction_stats(all_txns)
        job_summary = generate_narrative_summary(stats, job_id)
        db.add(job_summary)
        
        # Update job with success results
        job.status = "completed"
        job.row_count_raw = len(records)
        job.row_count_clean = clean_count
        job.completed_at = datetime.datetime.utcnow()
        db.commit()
        
        return f"Processed {len(records)} records for Job {job_id} (Clean: {clean_count})"
        
    except Exception as e:
        try:
            job = db.query(models.Job).filter(models.Job.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.datetime.utcnow()
                db.commit()
        except Exception as update_err:
            print(f"Failed to record job failure: {update_err}")
        return f"Job {job_id} failed: {str(e)}"
    finally:
        if 'file_path' in locals() and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"Cleaned up temporary upload file: {file_path}")
            except Exception as cleanup_err:
                print(f"Failed to delete temporary file {file_path}: {cleanup_err}")
        db.close()
