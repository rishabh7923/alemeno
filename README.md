# Transaction Processing & Analysis Engine

A FastAPI and Celery-based backend for asynchronously parsing, cleaning, and analyzing CSV transaction records, detecting anomalies, and generating category insights via LLM.

## High-Level Design

Below is the system architecture diagram illustrating the workflow between the API, worker, database, cache, and LLM:

![High-Level Design](docs/hll-design.png)

---

## Setup & Running Guide
This spins up the FastAPI API server, Celery Worker, PostgreSQL database, and Redis broker automatically.

1. **Move into the project directory**:
   ```bash
   cd alemeno
   ```
2. **Create/Configure your `.env` file**:
   Make sure you have your `.env` file in the project root with the correct `GEMINI_API_KEY`:
   ```env
   DATABASE_URL=postgresql://postgres:postgres@postgres:5432/assignment
   REDIS_URL=redis://redis:6379/0
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
3. **Build and Run**:
   ```bash
   docker compose up --build
   ```
4. The API server will be available at `http://localhost:8000`.
---

## API Documentation & Example Curl Requests

Once the server is running (at `http://localhost:8000`), you can access interactive documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### 1. Upload CSV to Start a Processing Job
Uploads a CSV file and enqueues a background parsing task. Returns a unique `job_id`.

**Request**:
```bash
curl -X POST "http://localhost:8000/jobs/upload" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@transactions.csv"
```
*Replace `transactions.csv` with the path to your CSV file.*

**Response**:
```json
{
  "job_id": 1
}
```

---

### 2. Check Job Processing Status & Summary
Get the current status (`pending`, `processing`, `completed`, or `failed`) and the high-level summary if completed.

**Request**:
```bash
curl -X GET "http://localhost:8000/jobs/1/status" \
  -H "accept: application/json"
```

**Response (When Processing)**:
```json
{
  "status": "processing"
}
```

**Response (When Completed)**:
```json
{
  "status": "completed",
  "summary": {
    "total_spend_inr": 12450.0,
    "total_spend_usd": 65.99,
    "top_merchants": [
      "Swiggy",
      "Uber",
      "Amazon"
    ],
    "anomaly_count": 1,
    "risk_level": "low"
  }
}
```

---

### 3. Retrieve Detailed Job Results
Gets the comprehensive list of cleaned transactions, flagged anomalies with reasons, category spend breakdown, and LLM generated analysis narrative.

**Request**:
```bash
curl -X GET "http://localhost:8000/jobs/1/results" \
  -H "accept: application/json"
```

**Response**:
```json
{
  "transactions": [
    {
      "id": 1,
      "txn_id": "TXN001",
      "date": "2026-06-01",
      "merchant": "Swiggy",
      "amount": 450.0,
      "currency": "INR",
      "status": "COMPLETED",
      "category": "Food",
      "account_id": "ACC123",
      "is_anomaly": false,
      "anomaly_reason": null
    },
    {
      "id": 2,
      "txn_id": "TXN002",
      "date": "2026-06-02",
      "merchant": "Amazon",
      "amount": 12000.0,
      "currency": "INR",
      "status": "COMPLETED",
      "category": "Shopping",
      "account_id": "ACC123",
      "is_anomaly": false,
      "anomaly_reason": null
    }
  ],
  "anomalies": [
    {
      "id": 5,
      "txn_id": "TXN005",
      "date": "2026-06-05",
      "merchant": "Ola",
      "amount": 50.0,
      "currency": "USD",
      "status": "COMPLETED",
      "category": "Transport",
      "account_id": "ACC123",
      "is_anomaly": true,
      "anomaly_reason": "USD transaction for domestic-only brand"
    }
  ],
  "spending": {
    "Food": {
      "INR": 450.0
    },
    "Shopping": {
      "INR": 12000.0
    }
  },
  "summary": "The main category of spending was Shopping, followed by Food. Total spends reached 12,450.00 INR and 65.99 USD. Risk profile is low based on the single domestic-brand currency mismatch anomaly detected."
}
```

---

### 4. List All Jobs
List all historical parsing jobs with options to filter by status (e.g. `?status=completed`).

**Request**:
```bash
curl -X GET "http://localhost:8000/jobs?status=completed" \
  -H "accept: application/json"
```

**Response**:
```json
[
  {
    "id": 1,
    "status": "completed",
    "filename": "transactions.csv",
    "row_count": 5,
    "created_at": "2026-06-24T01:00:00"
  }
]
```
