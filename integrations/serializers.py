from rest_framework import serializers

CURRENCY_HELP = "Three-letter ISO 4217 code, e.g. USD, EUR, INR."


class CurrencyField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("min_length", 3)
        kwargs.setdefault("max_length", 3)
        kwargs.setdefault("help_text", CURRENCY_HELP)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data).strip().upper()
        if not value.isalpha():
            raise serializers.ValidationError("Must be three letters.")
        return value


class CurrencyConversionSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, coerce_to_string=False
    )
    source_currency = CurrencyField()
    target_currency = CurrencyField()


class ConversionResultSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, coerce_to_string=False)
    source_currency = serializers.CharField()
    target_currency = serializers.CharField()
    rate = serializers.DecimalField(max_digits=18, decimal_places=8, coerce_to_string=False)
    converted = serializers.DecimalField(max_digits=24, decimal_places=4, coerce_to_string=False)
    rate_updated_at = serializers.CharField(allow_null=True)


class IntegrationSerializer(serializers.Serializer):
    slug = serializers.CharField()
    client = serializers.CharField()
    implemented = serializers.BooleanField()
    configured = serializers.BooleanField()
    detail = serializers.CharField(allow_blank=True)
