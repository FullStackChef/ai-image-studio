"""Stop Batch jobs and worker instances that outlived the job timeout.

EventBridge invokes this every five minutes. It is a safety net for a job
that never exits and for a root disk left behind after a failed scale-in.
"""

from __future__ import annotations

ACTIVE_JOB_STATUSES = {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"}


def stale_job_ids(jobs: list[dict], now_ms: int, max_age_ms: int) -> list[str]:
    stale = []
    for job in jobs:
        if job.get("status") not in ACTIVE_JOB_STATUSES:
            continue
        started = job.get("startedAt") or job.get("createdAt") or now_ms
        if now_ms - int(started) >= max_age_ms:
            stale.append(job["jobId"])
    return stale


def stale_instance_ids(instances: list[dict], now_epoch: int, max_age_seconds: int) -> list[str]:
    stale = []
    for instance in instances:
        if instance.get("state") not in {"pending", "running"}:
            continue
        if now_epoch - int(instance["launch_epoch"]) >= max_age_seconds:
            stale.append(instance["id"])
    return stale


def orphan_volume_ids(volumes: list[dict], now_epoch: int, min_age_seconds: int) -> list[str]:
    orphans = []
    for volume in volumes:
        if volume.get("state") != "available":
            continue
        if now_epoch - int(volume["create_epoch"]) >= min_age_seconds:
            orphans.append(volume["id"])
    return orphans


def handler(event, context):
    import os
    import time

    import boto3

    queue = os.environ["JOB_QUEUE"]
    max_age_seconds = int(os.environ["MAX_AGE_MINUTES"]) * 60
    tag_key = os.environ["WORKER_TAG_KEY"]
    tag_value = os.environ["WORKER_TAG_VALUE"]
    now = time.time()
    now_ms = int(now * 1000)
    batch = boto3.client("batch")
    ec2 = boto3.client("ec2")

    jobs = []
    for status in ACTIVE_JOB_STATUSES:
        token = None
        while True:
            kwargs = {"jobQueue": queue, "jobStatus": status, "maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            page = batch.list_jobs(**kwargs)
            jobs.extend(page.get("jobSummaryList", []))
            token = page.get("nextToken")
            if not token:
                break
    for job_id in stale_job_ids(jobs, now_ms, max_age_seconds * 1000):
        batch.terminate_job(jobId=job_id, reason="Exceeded ai-image-studio job timeout")

    described = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{tag_key}", "Values": [tag_value]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ]
    )
    instances = []
    for reservation in described.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instances.append(
                {
                    "id": instance["InstanceId"],
                    "state": instance["State"]["Name"],
                    "launch_epoch": int(instance["LaunchTime"].timestamp()),
                }
            )
    stale_instances = stale_instance_ids(instances, int(now), max_age_seconds + 15 * 60)
    if stale_instances:
        ec2.terminate_instances(InstanceIds=stale_instances)

    volume_page = ec2.describe_volumes(
        Filters=[
            {"Name": f"tag:{tag_key}", "Values": [tag_value]},
            {"Name": "status", "Values": ["available"]},
        ]
    )
    volumes = [
        {
            "id": volume["VolumeId"],
            "state": volume["State"],
            "create_epoch": int(volume["CreateTime"].timestamp()),
        }
        for volume in volume_page.get("Volumes", [])
    ]
    for volume_id in orphan_volume_ids(volumes, int(now), 30 * 60):
        ec2.delete_volume(VolumeId=volume_id)
    return {"terminatedJobs": len(stale_job_ids(jobs, now_ms, max_age_seconds * 1000))}
