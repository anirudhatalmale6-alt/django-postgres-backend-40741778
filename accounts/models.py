from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Email-as-identity user.

    Swapping the login field to email is the one change that is essentially
    impossible to retrofit later (it rewrites the auth table and every FK to
    it), so it is done up front. Username is kept as an optional display handle.

    Emails are stored lowercased and uniquely constrained — see
    UserManager.normalize_email and the save() override — so "A@x.com" and
    "a@x.com" cannot both register.
    """

    email = models.EmailField(_("email address"), unique=True, db_index=True)
    username = models.CharField(
        _("display name"), max_length=150, blank=True, default=""
    )
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")

    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Unset this instead of deleting an account."),
    )
    is_staff = models.BooleanField(_("staff status"), default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    EMAIL_FIELD = "email"

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Normalise here as well as in the manager: DRF serializers and the
        # admin both call save() directly without going through create_user.
        self.email = UserManager.normalize_email(self.email).lower()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_full_name(self) -> str:
        return self.full_name or self.username or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.username or self.email
