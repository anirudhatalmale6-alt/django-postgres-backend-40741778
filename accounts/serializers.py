from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Read shape for a user. Never includes the password hash."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "full_name",
            "is_active",
            "is_staff",
            "date_joined",
            "last_login",
        )
        read_only_fields = fields


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    class Meta:
        model = User
        fields = (
            "email",
            "username",
            "first_name",
            "last_name",
            "password",
            "password_confirm",
        )

    def validate_email(self, value):
        # The model lowercases on save, so uniqueness must be checked the same
        # way or "A@x.com" slips past a unique index holding "a@x.com".
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "The two password fields do not match."}
            )
        # Run Django's validators with a user instance attached so the
        # similarity validator can actually compare against email/name.
        candidate = User(
            email=attrs.get("email", ""),
            username=attrs.get("username", ""),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Editable subset of the user record.

    Email is intentionally absent: changing a login identity needs a
    verification round trip, not a PATCH.
    """

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name")


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login by email; returns the token pair plus the user object.

    The front end almost always needs the user's identity immediately after
    login. Returning it here saves a second round trip to /auth/me/.
    """

    username_field = User.USERNAME_FIELD

    def validate(self, attrs):
        # Normalise so login is case-insensitive, matching registration.
        if attrs.get(self.username_field):
            attrs[self.username_field] = attrs[self.username_field].strip().lower()
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        if user.check_password(value):
            raise serializers.ValidationError(
                "The new password must differ from the current one."
            )
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate(self, attrs):
        # Deliberately one generic error for a bad uid, an unknown user, an
        # expired token and a reused token: distinguishing them tells an
        # attacker which accounts exist and which links are still live.
        invalid = serializers.ValidationError(
            {"token": "The reset link is invalid or has expired."}
        )
        try:
            uid = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise invalid
        if not default_token_generator.check_token(user, attrs["token"]):
            raise invalid

        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})

        attrs["user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        # Changing the password rotates the hash, which is part of the token's
        # input, so the used link stops working from here on.
        return user


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MessageSerializer(serializers.Serializer):
    """Envelope for endpoints whose only payload is a human-readable message."""

    detail = serializers.CharField()
