"""Entry point used by Render Cron to refresh Facebook customers every 3 hours."""
from app import (
    SyncJob, app, db, get_facebook_token, init_db, logger, uuid,
    _run_facebook_sync,
)


def main():
    init_db()
    with app.app_context():
        if not get_facebook_token():
            logger.warning('Scheduled Facebook sync skipped: no Facebook token configured.')
            return

        active_job = SyncJob.query.filter(
            SyncJob.status.in_(('queued', 'running'))
        ).first()
        if active_job:
            logger.info('Scheduled Facebook sync skipped: job %s is still active.', active_job.id)
            return

        job = SyncJob(
            id=str(uuid.uuid4()), status='queued',
            message='Đồng bộ Facebook tự động mỗi 3 giờ...',
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    _run_facebook_sync(job_id)


if __name__ == '__main__':
    main()
