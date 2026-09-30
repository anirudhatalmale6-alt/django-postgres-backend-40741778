import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.conf import settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import (
    ChangePasswordSerializer,
    EmailTokenObtainPairSerializer,
    LogoutSerializer,
    MessageSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    RegistrationSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()

AUTH_TAG = ["auth"]


@extend_schema(
    tags=AUTH_TAG,
    summary="Register a new account",
    auth=[{}],
    responses={201: UserSerializer},
)
class RegisterView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        # Respond with the read serializer — the write one contains password
        # fields and no id.
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=AUTH_TAG,
    summary="Log in (obtain JWT pair)",
    description=(
        "Authenticate with email + password. Returns an `access` token (short "
        "lived, send as `Authorization: Bearer <access>`), a `refresh` token, "
        "and the authenticated user."
    ),
    auth=[{}],
)
class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"


@extend_schema(
    tags=AUTH_TAG,
    summary="Log out (blacklist a refresh token)",
    description=(
        "Blacklists the supplied refresh token so it can no longer be "
        "exchanged. Already-issued access tokens remain valid until they "
        "expire — that is inherent to stateless JWT; keep the access lifetime "
        "short (default 15 minutes)."
    ),
    request=LogoutSerializer,
    responses={205: OpenApiResponse(description="Token blacklisted.")},
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            # Raised, not returned: only exceptions reach the handler that
            # applies the shared error envelope.
            raise ValidationError(
                {"refresh": "The refresh token is invalid or already blacklisted."}
            ) from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=AUTH_TAG, summary="Current user")
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return ProfileUpdateSerializer
        return UserSerializer


@extend_schema(
    tags=AUTH_TAG,
    summary="Change password (authenticated)",
    request=ChangePasswordSerializer,
    responses={200: MessageSerializer},
)
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password updated."})


@extend_schema(
    tags=AUTH_TAG,
    summary="Request a password reset email",
    description=(
        "Always returns 200 with the same body whether or not the address is "
        "registered — a differing response would let anyone enumerate your "
        "user list. With the default console email backend the reset link is "
        "written to the server log and no mail is sent anywhere."
    ),
    request=PasswordResetRequestSerializer,
    auth=[{}],
    responses={200: MessageSerializer},
)
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            self._send_reset_email(user)
        else:
            logger.info("Password reset requested for unknown address %s", email)

        return Response(
            {
                "detail": (
                    "If an account exists for that address, a password reset "
                    "link has been sent."
                )
            }
        )

    @staticmethod
    def _send_reset_email(user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = f"{settings.FRONTEND_PASSWORD_RESET_URL}?uid={uid}&token={token}"
        minutes = settings.PASSWORD_RESET_TIMEOUT // 60
        send_mail(
            subject="Reset your password",
            message=(
                f"Hello,\n\n"
                f"Use the link below to choose a new password. "
                f"It expires in {minutes} minutes and can only be used once.\n\n"
                f"{reset_url}\n\n"
                f"If you did not request this, you can ignore this email.\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )


@extend_schema(
    tags=AUTH_TAG,
    summary="Confirm a password reset",
    request=PasswordResetConfirmSerializer,
    auth=[{}],
    responses={200: MessageSerializer},
)
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password has been reset."})
