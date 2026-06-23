import os
from celery import Celery


app = Celery(
    "tasks",
    broker=os.getenv("REDIS_URL"),
    backend=os.getenv("REDIS_URL")
)

app.autodiscover_tasks([ "app.tasks" ])

