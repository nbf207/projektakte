"""A review screen is ready only when its background job is no longer active."""
from .models import Job


def review_processing(document):
    return (not document.deleted_at and document.status!='accepted'
            and Job.objects.filter(document=document,status__in=['pending','running']).exists())
