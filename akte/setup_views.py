"""One-time, race-safe browser onboarding for the first owner account (0 users only)."""
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .operations import operation_gate


class FirstRunSetupForm(forms.Form):
    username = forms.CharField(
        label='Benutzername',
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        widget=forms.TextInput(attrs={'autocomplete': 'username', 'autofocus': True}),
    )
    password1 = forms.CharField(
        label='Passwort',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Mindestens 12 Zeichen, kein häufiges Standardpasswort.',
    )
    password2 = forms.CharField(
        label='Passwort wiederholen',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def clean(self):
        cleaned = super().clean()
        username = (cleaned.get('username') or '').strip()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Die beiden Passwörter stimmen nicht überein.')
        elif p1:
            candidate = get_user_model()(username=username)
            try:
                validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error('password1', exc)
        cleaned['username'] = username
        return cleaned


@require_http_methods(['GET', 'POST'])
def first_run_setup(request):
    User = get_user_model()
    if settings.DEMO_MODE or User.objects.exists():
        return HttpResponse('Nicht gefunden.', status=404)

    form = FirstRunSetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with operation_gate(), transaction.atomic():
            if User.objects.exists():
                return HttpResponse('Nicht gefunden.', status=404)
            user = User.objects.create_superuser(
                username=form.cleaned_data['username'],
                email='',
                password=form.cleaned_data['password1'],
            )
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('home')

    return render(request, 'setup.html', {'form': form})
