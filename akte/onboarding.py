"""Short-lived, one-use owner invitation."""
import hashlib
import secrets
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import OwnerInvitation
from .operations import guarded


@guarded
@transaction.atomic
def create_owner_invitation(username):
    if get_user_model().objects.filter(username=username).exists():
        raise ValidationError('Konto existiert bereits. Keine Zugangsdaten überschrieben.')
    user=get_user_model().objects.create_user(username=username,password=None)
    token=secrets.token_urlsafe(40)
    invite=OwnerInvitation.objects.create(user=user,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires=timezone.now()+timedelta(hours=24))
    return invite,token
